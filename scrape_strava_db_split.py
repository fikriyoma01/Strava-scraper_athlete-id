from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import datetime as dt
from tqdm import tqdm
from typing import Any, Dict, List, Optional
from mysql.connector import Error
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from sshtunnel import SSHTunnelForwarder, BaseSSHTunnelForwarderError
from contextlib import contextmanager
from collections import OrderedDict

# ──────────────────────────────────────────────────────────────────────────────
# Konfigurasi & helper MySQL
# ──────────────────────────────────────────────────────────────────────────────
from sshtunnel import SSHTunnelForwarder
import pymysql as mysql

SSH_CFG = dict(
    ssh_address_or_host=("...", 22),
    ssh_username="...",
    ssh_password="...",       
    remote_bind_address=("127.0.0.1", 3306),
    local_bind_address=("127.0.0.1", 0),     
    host_pkey_directories=[],
)

DB_BASE_CFG = dict(
    user="...",
    password="...",          
    database="...",
    charset="utf8mb4",
    cursorclass=mysql.cursors.DictCursor,
)

@contextmanager
def open_tunnel(max_retry: int = 3, delay: int = 5):
    """Context-manager membuka SSH tunnel dengan retry sederhana."""
    for attempt in range(1, max_retry + 1):
        try:
            tunnel = SSHTunnelForwarder(**SSH_CFG)
            tunnel.start()
            print(f"[SSH] Tunnel siap di localhost:{tunnel.local_bind_port}")
            try:
                yield tunnel
            finally:
                print("[SSH] Menutup tunnel …")
                tunnel.stop()
            return
        except BaseSSHTunnelForwarderError as exc:
            print(f"[SSH] Percobaan {attempt}/{max_retry} gagal: {exc}")
            if attempt == max_retry:
                raise
            time.sleep(delay)

def db_connect(tunnel: SSHTunnelForwarder):
    """Buka koneksi MySQL lewat tunnel yang sudah aktif."""
    cfg = dict(
        host="127.0.0.1",
        port=tunnel.local_bind_port,
        **DB_BASE_CFG,
    )
    try:
        conn = mysql.connect(**cfg)
        conn.autocommit(True)
        return conn
    except Error as e:
        sys.exit(f"[DB] Gagal koneksi: {e}")

def fetch_strava_ids(conn) -> List[str]:
    """
    Mengambil strava_id user yang:
      • Tercantum di kolom jersey_data (index JSON 0-4)
      • Pesanannya berstatus 'paid'
    """
    cur = conn.cursor()
    cur.execute(
        """
        WITH jersey_ids AS (
            SELECT o.id            AS order_id,
                   JSON_UNQUOTE(JSON_EXTRACT(o.jersey_data,'$[0].id')) AS user_id
            FROM   orders o
            WHERE  o.status = 'paid'
            UNION ALL
            SELECT o.id,
                   JSON_UNQUOTE(JSON_EXTRACT(o.jersey_data,'$[1].id'))
            FROM   orders o
            WHERE  o.status = 'paid'
            UNION ALL
            SELECT o.id,
                   JSON_UNQUOTE(JSON_EXTRACT(o.jersey_data,'$[2].id'))
            FROM   orders o
            WHERE  o.status = 'paid'
            UNION ALL
            SELECT o.id,
                   JSON_UNQUOTE(JSON_EXTRACT(o.jersey_data,'$[3].id'))
            FROM   orders o
            WHERE  o.status = 'paid'
            UNION ALL
            SELECT o.id,
                   JSON_UNQUOTE(JSON_EXTRACT(o.jersey_data,'$[4].id'))
            FROM   orders o
            WHERE  o.status = 'paid'
        )
        SELECT u.strava_id
        FROM   jersey_ids ji
        JOIN   users u ON u.id = ji.user_id
        WHERE  ji.user_id IS NOT NULL
              AND u.strava_id IS NOT NULL;
        """
    )
    ids = [str(row["strava_id"]) for row in cur.fetchall()]
    cur.close()
    return ids


def save_activity(conn, strava_id: str, act_id: str, payload: Dict[str, Any]):
    dist, elev, mv_sec, cal, cad, trainer, sport, elapsed_sec, pace_sec, pace_txt, disp_name, act_dt = extract_metrics(payload)

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO strava_activities
          (activity_id, strava_id, activity_date,
           distance_m, elev_gain_m, moving_time_s,
           calories, avg_cadence, trainer,
           sport_type, elapsed_time_s, pace_sec_per_km, pace_text,
           athlete_name, payload)
        VALUES
          (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          activity_date = VALUES(activity_date),
          distance_m    = VALUES(distance_m),
          elev_gain_m   = VALUES(elev_gain_m),
          moving_time_s = VALUES(moving_time_s),
          calories      = VALUES(calories),
          avg_cadence   = VALUES(avg_cadence),
          trainer       = VALUES(trainer),
          sport_type       = VALUES(sport_type),
          elapsed_time_s   = VALUES(elapsed_time_s),
          pace_sec_per_km  = VALUES(pace_sec_per_km),
          pace_text        = VALUES(pace_text),
          athlete_name  = VALUES(athlete_name),
          payload       = VALUES(payload),
          scraped_at    = CURRENT_TIMESTAMP;
        """,
        (
            act_id, strava_id, act_dt,
            dist, elev, mv_sec, 
            cal, cad, trainer, 
            sport, elapsed_sec, pace_sec, pace_txt,
            disp_name,
            json.dumps(payload),
        ),
    )
    cur.close()


# ──────────────────────────────────────────────────────────────────────────────
# Selenium / Chrome setup
# ──────────────────────────────────────────────────────────────────────────────
# VERSI BARU (SUDAH DIPERBAIKI)
def build_chrome(
    headless: bool,
    profile: Optional[str],
    chrome_bin: Optional[str],
) -> webdriver.Chrome:
    """Membangun instance Chrome dengan Selenium WebDriver Manager terintegrasi."""
    opts = Options()
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    args = [
        "--disable-gpu",
        "--window-size=1920,1080",
        "--lang=en-US",
        "--log-level=3",
    ]
    if profile:
        args.append(f"--user-data-dir={os.path.expanduser(profile)}")
    if headless:
        args.append("--headless=new")
    if chrome_bin:
        opts.binary_location = chrome_bin
    for a in args:
        opts.add_argument(a)

    try:
        # Selenium akan otomatis mengunduh & mengelola chromedriver yang sesuai
        return webdriver.Chrome(options=opts)
    except Exception as e:
        sys.exit(f"[!] Gagal memulai ChromeDriver: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Ekstraksi JSON / payload
# ──────────────────────────────────────────────────────────────────────────────
def _sanitize(raw: str) -> str:
    junk = "]) }while(1);</x>"
    return raw[len(junk) :] if raw.startswith(junk) else raw

def _find_json_in_scripts(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    sc = soup.find("script", id="__NEXT_DATA__")
    if sc and sc.string:
        try:
            return json.loads(_sanitize(sc.string))
        except Exception:
            pass
    for sc in soup.find_all("script", type="application/json"):
        txt = sc.get_text()
        if '"pageProps"' in txt and '"activity"' in txt:
            try:
                return json.loads(txt)
            except Exception:
                continue
    return {}

def _json_from_js_execution(drv: webdriver.Chrome) -> Dict[str, Any]:
    script = """
    try {
        var activityData = window.pageView.activity().attributes;
        var athleteData  = window.activityAthlete.attributes;
        activityData.athlete = athleteData;
        return activityData;
    } catch (e) { return null; }
    """
    try:
        result = drv.execute_script(script)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}

def _json_from_cdp(drv: webdriver.Chrome, act_id: str, timeout: int = 5) -> Dict[str, Any]:
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            logs = drv.get_log("performance")
        except Exception:
            return {}
        for entry in logs:
            msg = json.loads(entry["message"])["message"]
            if msg.get("method") != "Network.responseReceived":
                continue
            r = msg.get("params", {}).get("response", {})
            url = r.get("url", "")
            if f"api/v4/activities/{act_id}" in url:
                rid = msg["params"].get("requestId")
                if rid:
                    try:
                        bd = drv.execute_cdp_cmd(
                            "Network.getResponseBody", {"requestId": rid}
                        )
                        return json.loads(bd.get("body", "{}"))
                    except Exception:
                        pass
        time.sleep(0.3)
    return {}

def _extract_data_from_legacy_scripts(html_source: str) -> Dict[str, Any]:
    """Regex-based fallback extractor."""
    try:
        # gabungkan blok aktivitas
        all_txt = ""
        for m in re.finditer(
            r"pageView\.activity\(\)\.set\((.*?)\);", html_source, re.DOTALL
        ):
            all_txt += m.group(1)

        if not all_txt:
            return {}

        patterns = {
            "distance": r"distance\s*:\s*([\d.]+)",
            "elev_gain": r"elev_gain\s*:\s*([\d.]+)",
            "moving_time": r"moving_time\s*:\s*(\d+)",
            "calories": r"calories\s*:\s*([\d.]+)",
            "avg_hr": r"avg_hr\s*:\s*([\d.]+)",
            "avg_cadence": r"avg_cadence\s*:\s*([\d.]+)",
            "trainer": r"trainer\s*:\s*(true|false)",
        }

        extracted: Dict[str, Any] = {}
        for key, pattern in patterns.items():
            m = re.search(pattern, all_txt)
            if m:
                val = m.group(1)
                if key == "trainer":
                    extracted[key] = val.lower() == "true"
                elif "." in val:
                    extracted[key] = float(val)
                else:
                    extracted[key] = int(val)

        # athlete
        m_ath = re.search(r"new Strava\.Models\.Athlete\((.*?)\);", html_source, re.DOTALL)
        if m_ath:
            extracted["athlete"] = json.loads(m_ath.group(1).strip())

        if "distance" in extracted and "moving_time" in extracted:
            return extracted
    except Exception as e:
        print(f"[!] Regex extraction error: {e}", file=sys.stderr)
    return {}

def get_activity_payload(
    drv: webdriver.Chrome,
    act_id: str,
    wait: int,
    use_cdp: bool
) -> Dict[str, Any]:

    time.sleep(5)
    html_source = drv.page_source
    act: Dict[str, Any] = {}

    # 1) Modern NEXT.js
    data = _find_json_in_scripts(html_source)
    act = data.get("props", {}).get("pageProps", {}).get("activity", {})

    # 2) Legacy JS
    if not act:
        act = _json_from_js_execution(drv)

    # 3) Regex fallback lama
    if not act:
        act = _extract_data_from_legacy_scripts(html_source)

    # 4) CDP
    if not act and use_cdp:
        act = _json_from_cdp(drv, act_id)

    # ➊ tanggal dari <time>
    if "start_date" not in act:
        m = re.search(r'<time[^>]*>\s*([^<]+?)\s*</time>', html_source, re.I)
        if m:
            raw = m.group(1).strip()
            dt_obj = None
            for fmt in ("%I:%M %p on %A, %B %d, %Y",   # 2)
                        "%A, %B %d, %Y"):               # 1)
                try:
                    dt_obj = dt.datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            if dt_obj:
                # simpan full timestamp MySQL: YYYY-MM-DD HH:MM:SS
                act["start_date"] = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
            else:
                # format belum dikenali → abaikan (atau simpan string mentah)
                act["start_date"] = None
                
    # === Tambahan: ambil <time> + sport/elapsed/pace ================

    soup = BeautifulSoup(html_source, "html.parser")

    # ➋ sport type  (Walk / Run / Ride …)
    if "sport_type" not in act:
        title_span = soup.select_one("span.title")
        if title_span and "–" in title_span.text:
            act["sport_type"] = title_span.text.split("–")[-1].strip()

    # ➌ elapsed time
    if "elapsed_time" not in act:
        lab = soup.find("span", string=re.compile("Elapsed Time", re.I))
        if lab:
            strong = lab.find_parent().find_next("strong")
            if strong:
                txt = strong.text.strip()
                act["elapsed_time"]      = txt
                act["elapsed_time_sec"]  = _hms_to_sec(txt)

    # ➍ pace
    if "pace_per_km" not in act:
        lab = soup.find("span", string=re.compile(r"\bPace\b", re.I))
        if lab:
            strong = lab.find_previous("strong")  # li > strong sebelum span.label
            if strong:
                txt = strong.text.strip()         # '9:42 /km'
                act["pace_per_km"]     = txt
                act["pace_sec_per_km"] = _pace_to_sec(txt)

    return act


# ───────── helper parsers ─────────
def _to_int(v) -> Optional[int]:
    return int(round(float(v))) if v is not None else None

def extract_metrics(payload: Dict[str, Any]):
    # ── tanggal ────────────────────────────────────────────────────
    date_iso = payload.get("start_date") or payload.get("startDate")
    activity_dt = (date_iso or "").replace("Z", "").replace("T", " ") \
                 if date_iso else None

    # ── metrik utama ──────────────────────────────────────────────
    dist        = _to_int(payload.get("distance"))
    elev        = _to_int(payload.get("elev_gain"))
    moving_sec  = _to_int(payload.get("moving_time"))

    # ── metrik baru ───────────────────────────────────────────────
    calories    = payload.get("calories")
    cadence     = payload.get("avg_cadence")
    trainer     = 1 if payload.get("trainer") else 0
    sport       = payload.get("sport_type")
    elapsed_sec = _to_int(payload.get("elapsed_time_sec"))
    pace_sec    = _to_int(payload.get("pace_sec_per_km"))
    pace_txt    = payload.get("pace_per_km")

    # ── nama atlet ────────────────────────────────────────────────
    athlete_name = payload.get("athlete", {}).get("display_name")

    return (
        dist, elev, moving_sec,
        calories, cadence, trainer,
        sport, elapsed_sec, pace_sec, pace_txt,
        athlete_name, activity_dt,
    )
    
# VERSI BARU (SUDAH DIPERBAIKI)
def _hms_to_sec(s: str) -> Optional[int]:
    """'1:23:45' → 5025  |  '40:41' → 2441 | '9:02 /km' -> (diabaikan dengan aman)"""
    try:
        # Bersihkan setiap bagian dari karakter non-digit sebelum konversi
        parts = [int(re.sub(r'\D', '', part)) for part in s.strip().split(":")]
        if   len(parts) == 3: h, m, s = parts; return h*3600 + m*60 + s
        elif len(parts) == 2: m, s = parts;   return m*60 + s
        return None
    except (ValueError, TypeError):
        # Jika gagal (misal format tidak terduga), kembalikan None
        return None

# VERSI BARU (SUDAH DIPERBAIKI)
def _pace_to_sec(s: str) -> Optional[int]:
    """'9:42 /km' → 582 detik. Lebih tahan terhadap variasi format."""
    try:
        # Ekstrak hanya angka dari string pace
        matches = re.findall(r'\d+', s)
        if len(matches) >= 2:
            minutes = int(matches[0])
            seconds = int(matches[1])
            return minutes * 60 + seconds
        return None
    except (ValueError, TypeError, IndexError):
        return None

# ──────────────────────────────────────────────────────────────────────────────
# Ambil aktivitas terbaru per atlet
# ──────────────────────────────────────────────────────────────────────────────

def recent_activity_ids(
    drv: webdriver.Chrome,
    athlete_id: str,
    *,
    limit: int,
    wait: int,
    scroll: int = 6,
) -> List[str]:
    url = f"https://www.strava.com/athletes/{athlete_id}"
    drv.get(url)

    WebDriverWait(drv, wait).until(
        lambda d: f"/athletes/{athlete_id}" in d.current_url
    )
    html = drv.page_source

    # ── DETEKSI PROFIL PRIVAT / TIDAK ADA ──────────────────────────
    if ("limited_profile" in html or
        "This profile is private" in html or
        "page you requested is not available" in html):
        raise RuntimeError("Profil di-private / tidak ditemukan")

    # 0️⃣ regex Activity-<id> di script feed ------------------------
    ids_re = re.findall(r'Activity-(\d{6,})', html)
    if ids_re:
        ids_sorted = sorted({int(i) for i in ids_re}, reverse=True)
        return [str(i) for i in ids_sorted[:limit]]

    # 1️⃣ window.__NEXT_DATA__ --------------------------------------
    nodes = drv.execute_script("""
        try {
          return window.__NEXT_DATA__
            ?.props?.pageProps?.athlete?.recentActivities?.nodes || [];
        } catch(e) { return []; }
    """)
    if nodes:
        nodes.sort(key=lambda n: int(n["id"]), reverse=True)
        return [str(n["id"]) for n in nodes[:limit] if n.get("id")]

    # 2️⃣ DOM + scroll fallback ------------------------------------
    def _extract_ids_js():
        return drv.execute_script("""
            return Array.from(
                document.querySelectorAll('a[href^="/activities/"]')
            ).map(a => (a.getAttribute('href').match(/\\d+/) || [])[0])
             .filter(Boolean);
        """)
    ids = OrderedDict()
    for _ in range(max(scroll, 1)):
        for aid in _extract_ids_js():
            ids[aid] = None
            if len(ids) >= limit:
                break
        if len(ids) >= limit:
            break
        drv.execute_script("window.scrollBy(0, window.innerHeight);")
        time.sleep(1.2)

    if not ids:
        raise RuntimeError("Tidak ada aktivitas ditemukan.")
    return sorted(ids.keys(), key=int, reverse=True)[:limit]

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser("Strava scraper + MySQL")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--athlete-id", nargs="*", help="Override: ID atlet manual.")
    src.add_argument("--athletes-file", help="File teks daftar atlet.")
    
    # --- ARGUMEN BARU UNTUK PEMBAGIAN ---
    ap.add_argument("--total-shards", type=int, default=1, help="Total jumlah pembagian (misal: 3 untuk 3 PC).")
    ap.add_argument("--shard-id", type=int, default=1, help="Bagian yang akan dijalankan oleh PC ini (misal: 1, 2, atau 3).")
    # ------------------------------------

    ap.add_argument("--per-athlete", type=int, default=1, help="Jumlah aktivitas terbaru per atlet.")
    ap.add_argument("--headless", action="store_true", help="Headless Chrome.")
    ap.add_argument("--wait", type=int, default=10, help="Timeout load halaman.")
    ap.add_argument("--chrome-profile", help="Path Chrome profile login Strava.")
    ap.add_argument("--chromedriver", help="Path chromedriver (opsional).")
    ap.add_argument("--chrome-binary", help="Path chrome.exe custom.")
    ap.add_argument("--use-cdp", action="store_true", help="Gunakan fallback CDP.")
    args = ap.parse_args()

    # --- Validasi Argumen Pembagian ---
    if args.shard_id > args.total_shards or args.shard_id < 1:
        sys.exit("[!] --shard-id harus di antara 1 dan --total-shards.")
    # ------------------------------------

    with open_tunnel() as tunnel:
        conn = db_connect(tunnel)

        athlete_ids: List[str] = []
        if args.athlete_id:
            athlete_ids.extend(args.athlete_id)
        elif args.athletes_file and os.path.isfile(args.athletes_file):
            with open(args.athletes_file) as f:
                athlete_ids.extend([l.strip() for l in f if l.strip().isdigit()])
        else:
            athlete_ids = fetch_strava_ids(conn)

        if not athlete_ids:
            sys.exit("[!] Tidak ada atlet (strava_id) ditemukan.")

        print(f"[i] Total {len(athlete_ids)} atlet ditemukan di database.")

        # --- LOGIKA PEMBAGIAN BEBAN KERJA ---
        if args.total_shards > 1:
            total_athletes = len(athlete_ids)
            # Menghitung ukuran setiap bagian dan sisa
            chunk_size = total_athletes // args.total_shards
            remainder = total_athletes % args.total_shards
            
            # Menentukan indeks awal dan akhir untuk shard ini
            start_index = (args.shard_id - 1) * chunk_size + min(args.shard_id - 1, remainder)
            end_index = start_index + chunk_size + (1 if args.shard_id <= remainder else 0)

            # Ambil hanya bagian yang sesuai untuk PC ini
            processed_ids = athlete_ids[start_index:end_index]
            
            print(f"[i] Menjalankan bagian {args.shard_id} dari {args.total_shards}.")
            print(f"[i] PC ini akan memproses {len(processed_ids)} atlet (dari indeks {start_index} sampai {end_index-1}).")
            athlete_ids = processed_ids
        else:
            print(f"[i] Memproses semua {len(athlete_ids)} atlet.")
        # ------------------------------------

        if not athlete_ids:
            sys.exit("[!] Tidak ada atlet yang perlu diproses untuk bagian ini.")

        drv = build_chrome(
            headless=args.headless,
            profile=args.chrome_profile,
            chrome_bin=args.chrome_binary,
        )

        try:
            for ath_id in tqdm(
                athlete_ids,
                desc=f"Scraping athletes (Shard {args.shard_id}/{args.total_shards})",
                unit="athlete",
                ncols=80,
                leave=True,
            ):
                # ... (sisa loop utama tetap sama) ...
                try:
                    act_ids = recent_activity_ids(
                        drv, ath_id,
                        limit=args.per_athlete,
                        wait=args.wait,
                    )
                except Exception as e:
                    print(f"[!] Gagal ambil recent {ath_id}: {e}", file=sys.stderr)
                    continue

                for act_id in tqdm(
                    act_ids,
                    desc=f"Ath {ath_id}",
                    unit="act",
                    ncols=70,
                    leave=False,
                ):
                    try:
                        drv.get(f"https://www.strava.com/activities/{act_id}")
                        payload = get_activity_payload(
                            drv, act_id, wait=args.wait, use_cdp=args.use_cdp
                        )
                        if not payload:
                            print(f"[!] payload kosong {act_id}")
                            continue

                        pl_ath = str(
                            payload.get("athlete", {}).get("id") or
                            payload.get("athlete_id", "")
                        )
                        if pl_ath and pl_ath != str(ath_id):
                            print(f"[!] Skip {act_id}: milik atlet {pl_ath}, bukan {ath_id}")
                            continue
                        
                        save_activity(conn, ath_id, act_id, payload)
                        print(f"[DB] activity {act_id} ⇒ DB OK")
                    except Exception as e:
                        print(f"[!] Error scrape {act_id}: {e}", file=sys.stderr)
        finally:
            drv.quit()
            conn.close()
            print("[✔] Selesai.")

if __name__ == "__main__":
    main()
