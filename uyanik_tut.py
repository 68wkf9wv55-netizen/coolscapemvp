"""
CoolScape AI – Streamlit Community Cloud uygulamasını uyanık tutar.
Streamlit Cloud, 12 saat ziyaret almayan uygulamayı uyutur. Bu betik uygulamayı gerçek bir tarayıcıyla açar
(sayılan bir ziyaret olur); uygulama uyuyorsa "Yes, get this app back up!" düğmesine basar.
GitHub Actions'ta .github/workflows/uyanik_tut.yml ile 3 saatte bir çalışır.
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("UYGULAMA_URL", "https://cop31so-bjfwfgycfoqz7ltvuqiz5f.streamlit.app/")
DUGME = "Yes, get this app back up"

with sync_playwright() as p:
    tarayici = p.chromium.launch()
    sayfa = tarayici.new_page()
    sayfa.goto(URL, wait_until="domcontentloaded", timeout=90_000)
    sayfa.wait_for_timeout(8_000)
    dugme = sayfa.get_by_role("button", name=DUGME)
    if dugme.count():
        print("Uygulama uyuyordu; uyandırılıyor.")
        dugme.first.click()
        try:
            dugme.first.wait_for(state="detached", timeout=180_000)
        except Exception:
            print("Uyandırma düğmesi kaybolmadı; uygulama kayıtlarını kontrol edin.")
            tarayici.close()
            sys.exit(1)
        sayfa.wait_for_timeout(60_000)   # uygulama açılsın, ziyaret sayılsın
        print("Uyandırıldı.")
    else:
        sayfa.wait_for_timeout(30_000)   # oturum açık kalsın, ziyaret sayılsın
        print("Uygulama zaten açık.")
    tarayici.close()
