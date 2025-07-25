# Strava Activity Scraper 🚀

Skrip Python ini dirancang untuk melakukan scraping data aktivitas dari Strava. Skrip ini menggunakan Selenium untuk mengotomatisasi browser Chrome, terhubung ke database MySQL melalui SSH Tunnel, mengambil daftar ID atlet, mengambil data aktivitas terbaru mereka, dan menyimpannya kembali ke database.

## Fitur Utama ✨
- **Koneksi Database Aman**: Menggunakan SSHTunnelForwarder untuk koneksi SSH tunnel ke MySQL.
- **Pengambilan Data Fleksibel**: Dapat mengambil daftar atlet dari database, file teks, atau argumen command-line.
- **Ekstraksi Data Cerdas**: Menggunakan beberapa metode untuk mengekstrak data JSON dari halaman Strava (termasuk dari `__NEXT_DATA__`, eksekusi JS, dan fallback ke Chrome DevTools Protocol).
- **Penyimpanan Data**: Menyimpan hasil scraping ke dalam tabel `strava_activities` dengan penanganan data duplikat (`INSERT ... ON DUPLICATE KEY UPDATE`).
- **Pembagian Beban Kerja (Sharding)**: Memungkinkan pembagian daftar atlet untuk diproses secara paralel di beberapa mesin/PC.
- **Login Otomatis**: Memanfaatkan profil Chrome yang sudah ada untuk melewati proses login Strava secara manual.

---

## 1. Prasyarat
Pastikan sistem Anda telah terinstal:

- Python 3.8+
- Google Chrome (browser)
- Akses ke server SSH dan database MySQL yang sesuai.

---

## 2. Instalasi ⚙️

### Langkah 1: Clone Repositori
```bash
git clone <URL_REPOSITORI_ANDA>
cd <NAMA_DIREKTORI>
````

### Langkah 2: Buat dan Instal Dependensi

Buat file bernama `requirements.txt` dan salin daftar dependensi di bawah ini ke dalamnya:

```text
beautifulsoup4
pymysql
selenium
sshtunnel
tqdm
```

Kemudian, instal semua dependensi menggunakan pip:

```bash
pip install -r requirements.txt
```

### Langkah 3: Konfigurasi Skrip ⚠️

Buka file skrip Python Anda dan ubah kredensial SSH dan Database di bagian atas file.

```python
# Ganti dengan kredensial Anda
SSH_CFG = dict(
    ssh_address_or_host=("ALAMAT_IP_SSH", 22),
    ssh_username="USER_SSH",
    ssh_password="PASSWORD_SSH",
    # ... (sisa konfigurasi biarkan default jika tidak tahu)
)

# Ganti dengan kredensial database Anda
DB_BASE_CFG = dict(
    user="USER_DB",
    password="PASSWORD_DB",
    database="NAMA_DATABASE",
    # ... (sisa konfigurasi biarkan default)
)
```

**PERINGATAN KEAMANAN:** Jangan pernah menyimpan kredensial asli di dalam kode yang dikontrol versi (seperti Git). Gunakan variabel lingkungan atau metode manajemen rahasia lainnya untuk produksi.

### Langkah 4: Siapkan Profil Chrome

Skrip ini memerlukan login ke Strava. Cara termudah adalah dengan menggunakan profil Chrome yang sudah ada di mana Anda sudah login ke Strava.

1. Buka Google Chrome di PC Anda.
2. Pastikan Anda sudah login ke `strava.com`.
3. Ketik `chrome://version` di address bar dan tekan Enter.
4. Cari baris **Profile Path** dan salin path tersebut.

#### Contoh Profil Path

* **Windows:** `C:\Users\NamaAnda\AppData\Local\Google\Chrome\User Data\Default`
* **macOS:** `/Users/NamaAnda/Library/Application Support/Google/Chrome/Default`
* **Linux:** `/home/namaanda/.config/google-chrome/default`

Anda akan menggunakan path ini untuk argumen `--chrome-profile`.

---

## 3. Struktur Database

Pastikan tabel `strava_activities` ada di database Anda. Anda bisa menggunakan query SQL berikut untuk membuatnya:

```sql
CREATE TABLE `strava_activities` (
  `activity_id` BIGINT UNSIGNED NOT NULL,
  `strava_id` BIGINT UNSIGNED NOT NULL,
  `activity_date` DATETIME DEFAULT NULL,
  `distance_m` INT DEFAULT NULL,
  `elev_gain_m` INT DEFAULT NULL,
  `moving_time_s` INT DEFAULT NULL,
  `calories` FLOAT DEFAULT NULL,
  `avg_cadence` FLOAT DEFAULT NULL,
  `trainer` TINYINT(1) DEFAULT 0,
  `sport_type` VARCHAR(50) DEFAULT NULL,
  `elapsed_time_s` INT DEFAULT NULL,
  `pace_sec_per_km` INT DEFAULT NULL,
  `pace_text` VARCHAR(20) DEFAULT NULL,
  `athlete_name` VARCHAR(255) DEFAULT NULL,
  `payload` JSON DEFAULT NULL,
  `scraped_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`activity_id`),
  KEY `strava_id` (`strava_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

## 4. Penggunaan 💻

Jalankan skrip dari terminal. Argumen yang paling penting adalah `--chrome-profile`.

### Contoh Dasar

```bash
python nama_skrip.py --chrome-profile "C:\Users\NamaAnda\AppData\Local\Google\Chrome\User Data\Default"
```

### Menjalankan dalam Mode Headless

```bash
python nama_skrip.py --headless --chrome-profile "/path/to/your/chrome/profile"
```

### Menentukan Atlet Secara Manual

```bash
python nama_skrip.py --athlete-id 1234567 9876543 --per-athlete 5 --chrome-profile "..."
```

### Menggunakan File untuk Daftar Atlet

```bash
python nama_skrip.py --athletes-file athletes.txt --chrome-profile "..."
```

### Pembagian Beban Kerja (Sharding)

Misalnya untuk 3 PC:

* **PC 1:**

  ```bash
  python nama_skrip.py --total-shards 3 --shard-id 1 --chrome-profile "..."
  ```
* **PC 2:**

  ```bash
  python nama_skrip.py --total-shards 3 --shard-id 2 --chrome-profile "..."
  ```
* **PC 3:**

  ```bash
  python nama_skrip.py --total-shards 3 --shard-id 3 --chrome-profile "..."
  ```

Skrip akan secara otomatis membagi daftar atlet menjadi bagian sesuai konfigurasi.

---

## 5. Daftar Argumen

| Argumen            | Deskripsi                                                                                    | Default |
| ------------------ | -------------------------------------------------------------------------------------------- | ------- |
| `--athlete-id`     | Satu atau lebih ID atlet Strava untuk diproses. Saling eksklusif dengan `--athletes-file`.   | -       |
| `--athletes-file`  | Path ke file teks yang berisi daftar ID atlet (satu per baris).                              | -       |
| `--total-shards`   | Jumlah total bagian untuk pembagian kerja.                                                   | 1       |
| `--shard-id`       | ID bagian yang akan dijalankan oleh instansi skrip ini (1 hingga `total-shards`).            | 1       |
| `--per-athlete`    | Jumlah aktivitas terbaru yang akan diambil untuk setiap atlet.                               | 1       |
| `--headless`       | Menjalankan Google Chrome dalam mode headless (tanpa GUI).                                   | False   |
| `--wait`           | Waktu tunggu maksimum (detik) untuk halaman dimuat.                                          | 10      |
| `--chrome-profile` | (Penting) Path ke direktori profil Google Chrome Anda untuk menggunakan sesi login yang ada. | -       |
| `--chrome-binary`  | Path ke file eksekusi `chrome.exe` jika tidak berada di lokasi standar.                      | -       |
| `--use-cdp`        | Gunakan Chrome DevTools Protocol sebagai fallback jika metode lain gagal mengekstrak data.   | False   |

```
```
