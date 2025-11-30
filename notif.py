import os
import time
import requests
from bs4 import BeautifulSoup
from telegram import Bot

# ==========
# KONFIGURASI
# ==========
BASE_URL = "https://nde.posindonesia.co.id"

# Credentials - MUST be set via environment variables
NDE_USERNAME = os.getenv("NDE_USERNAME")
NDE_PASSWORD = os.getenv("NDE_PASSWORD")
NDE_LOGIN_URL = "https://nde.posindonesia.co.id/login"    # sesuaikan
NDE_DASHBOARD_URL = "https://nde.posindonesia.co.id"      # sesuaikan

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL_SECONDS = 1800  # cek tiap 60 detik

# =======================================
# TELEGRAM NOTIF (sinkron, simpel)
# =======================================

def kirim_notif_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ BOT TOKEN / CHAT ID belum diset!")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}

    try:
        r = requests.post(url, data=data)
        if r.status_code != 200:
            print("⚠️ Telegram tidak merespon 200:", r.text)
    except Exception as e:
        print("❌ Gagal kirim notif:", e)


# =======================================
# LOGIN VIA NEXTAUTH (csrf + credentials)
# =======================================

def login_nextauth_and_get_dashboard_html() -> str:
    """
    Flow:
      1. GET /api/auth/csrf → ambil csrfToken
      2. POST /api/auth/callback/credentials?json=true
      3. (opsional) cek /api/auth/session
      4. GET /dashboard → kembalikan HTML
    """

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "text/html,application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{BASE_URL}/login",
    }

    with requests.Session() as s:
        # ---------- 1. GET CSRF ----------
        try:
            resp_csrf = s.get(f"{BASE_URL}/api/auth/csrf", headers=headers, verify=False)
            resp_csrf.raise_for_status()
        except Exception as e:
            print("❌ Gagal GET /api/auth/csrf:", e)
            return ""

        try:
            csrf_json = resp_csrf.json()
            csrf_token = csrf_json.get("csrfToken")
        except Exception as e:
            print("❌ Gagal parsing csrfToken:", e)
            return ""

        if not csrf_token:
            print("❌ csrfToken kosong!")
            return ""

        # ---------- 2. POST CREDENTIALS ----------
        payload = {
            "csrfToken": csrf_token,
            "callbackUrl": "/",
            "username": NDE_USERNAME,
            "password": NDE_PASSWORD,
            "json": "true",
        }

        try:
            resp_login = s.post(
                f"{BASE_URL}/api/auth/callback/credentials?json=true",
                data=payload,
                headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                allow_redirects=True,
                verify=False,
            )
        except Exception as e:
            print("❌ Gagal POST /api/auth/callback/credentials:", e)
            return ""

        if resp_login.status_code != 200:
            print("❌ Login credentials status bukan 200:", resp_login.status_code)
            return ""

        # Respon bisa JSON / non-JSON, jadi jangan keras
        try:
            login_json = resp_login.json()
            if login_json.get("error"):
                print("⚠️ Respon login mengandung error:", login_json)
                return ""
        except Exception:
            pass

        # ---------- 3. (Opsional) CEK SESSION ----------
        try:
            s.get(f"{BASE_URL}/api/auth/session", headers=headers, verify=False)
        except Exception as e:
            print("⚠️ Tidak bisa GET /api/auth/session:", e)

        # ---------- 4. GET DASHBOARD ----------
        try:
            resp_dashboard = s.get(f"{BASE_URL}/dashboard", headers=headers, verify=False)
            resp_dashboard.raise_for_status()
        except Exception as e:
            print("❌ Gagal GET /dashboard:", e)
            return ""

        return resp_dashboard.text


# =======================================
# PARSER KHUSUS KARTU SURAT MASUK & DISPOSISI
# =======================================

def _extract_count_by_label(html: str, label_text: str) -> int:
    """
    Util: untuk kartu dengan struktur:
        <div class="grow">
          <p>Label</p>
          <p class="text-4xl">X</p>
          <p>Total: Y</p>
        </div>
    label_text contoh: "Surat Masuk" atau "Disposisi"
    return: X (angka besar)
    """

    soup = BeautifulSoup(html, "html.parser")

    # 1. cari <p>Label</p>
    label = soup.find("p", string=lambda t: t and label_text in t)
    if not label:
        print(f"❌ Tidak menemukan label '{label_text}'.")
        return -1

    # 2. parent div.grow
    parent = label.find_parent("div", class_="grow")
    if not parent:
        print(f"❌ Tidak menemukan parent 'div.grow' untuk '{label_text}'.")
        return -1

    # 3. ambil semua <p> di dalam grow
    ps = parent.find_all("p")
    if len(ps) < 2:
        print(f"❌ Tidak cukup elemen <p> untuk '{label_text}'.")
        return -1

    # p kedua = angka besar
    angka_text = ps[1].get_text(strip=True)

    try:
        return int(angka_text)
    except Exception:
        print(f"❌ Gagal parsing angka '{angka_text}' untuk '{label_text}'.")
        return -1


def extract_surat_masuk_count(html: str) -> int:
    return _extract_count_by_label(html, "Surat Masuk")


def extract_disposisi_count(html: str) -> int:
    return _extract_count_by_label(html, "Disposisi")


# =======================================
# LOOP UTAMA
# =======================================

def main():
    # Validate required environment variables
    required_vars = {
        "NDE_USERNAME": NDE_USERNAME,
        "NDE_PASSWORD": NDE_PASSWORD,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID
    }
    
    missing_vars = [var for var, value in required_vars.items() if not value]
    if missing_vars:
        print(f"❌ ERROR: Missing required environment variables: {', '.join(missing_vars)}")
        print("Please set these variables in your .env file or environment.")
        return
    
    print("🚀 Bot Monitoring NDE dimulai...")

    last_surat_masuk = None
    last_disposisi = None

    while True:
        try:
            html = login_nextauth_and_get_dashboard_html()
            if not html:
                print("⚠️ Tidak mendapatkan HTML dashboard. Cek error di atas.")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            surat_masuk = extract_surat_masuk_count(html)
            disposisi = extract_disposisi_count(html)

            print(f"[DEBUG] Surat Masuk: {surat_masuk}, Disposisi: {disposisi}")

            # Jika parsing gagal, skip loop ini
            if surat_masuk < 0 and disposisi < 0:
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            # Inisialisasi pertama kali
            if last_surat_masuk is None and last_disposisi is None:
                last_surat_masuk = surat_masuk
                last_disposisi = disposisi
                kirim_notif_telegram(
                    f"Bot aktif.\n"
                    f"📥 Surat Masuk: {surat_masuk}\n"
                    f"📨 Disposisi: {disposisi}"
                )
            else:
                # Cek Surat Masuk baru
                if surat_masuk >= 0 and last_surat_masuk is not None and surat_masuk > last_surat_masuk:
                    baru = surat_masuk - last_surat_masuk
                    kirim_notif_telegram(
                        f"📩 Ada {baru} Surat Masuk baru!\n"
                        f"Total Surat Masuk sekarang: {surat_masuk}"
                    )
                    last_surat_masuk = surat_masuk

                # Cek Disposisi baru
                if disposisi >= 0 and last_disposisi is not None and disposisi > last_disposisi:
                    baru = disposisi - last_disposisi
                    kirim_notif_telegram(
                        f"📨 Ada {baru} Disposisi baru!\n"
                        f"Total Disposisi sekarang: {disposisi}"
                    )
                    last_disposisi = disposisi

                # Update kalau berkurang (tanpa notif atau bisa kamu tambahkan sendiri)
                if surat_masuk >= 0 and last_surat_masuk is not None and surat_masuk < last_surat_masuk:
                    last_surat_masuk = surat_masuk
                if disposisi >= 0 and last_disposisi is not None and disposisi < last_disposisi:
                    last_disposisi = disposisi

        except Exception as e:
            print("❌ ERROR tak terduga:", e)
            kirim_notif_telegram(f"⚠️ Bot error: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()