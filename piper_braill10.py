#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import requests
import subprocess
import threading
from threading import Thread, Lock, Event
import RPi.GPIO as GPIO
import tempfile
import wave

# ==================== KONFİGÜRASYON ====================
GITHUB_REPO = "mehkerer8/pdfs"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
LOCAL_BOOKS_DIR = "/home/pixel/braille_books"
UPDATE_INTERVAL = 3600

# PIPER TTS AYARLARI 
PIPER_BINARY_PATH = "./piper/piper"  # Piper binary dosyasının yolu
PIPER_MODEL_PATH = "./tr_TR-fettah-medium.onnx"  # Model dosyası

# ==================== PİPER TTS SES SİSTEMİ ====================
class VoiceEngine:
    def __init__(self):
        self.setup()
    
    def setup(self):
        """Piper TTS sistemini kur"""
        print("🔊 Piper TTS sistemi kuruluyor...")
        
        # Piper binary kontrolü
        if not os.path.exists(PIPER_BINARY_PATH):
            print("❌ Piper binary bulunamadı!")
            print("Lütfen şu komutla indirin:")
            print("  cd /home/pixel && mkdir -p piper")
            print("  cd /home/pixel/piper")
            print("  wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64")
            print("  mv piper_linux-arm64 piper")
            print("  chmod +x piper")
            raise FileNotFoundError("Piper binary bulunamadı")
        
        # Model kontrolü
        if not os.path.exists(PIPER_MODEL_PATH):
            print("❌ Piper modeli bulunamadı!")
            print("Lütfen şu komutla indirin:")
            print("  mkdir -p /home/pixel/piper_models")
            print("  cd /home/pixel/piper_models")
            print("  wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-rüştü-hoca-tts-high.onnx")
            raise FileNotFoundError("Piper modeli bulunamadı")
        
        # Piper binary çalıştırılabilir mi kontrol et
        try:
            result = subprocess.run([PIPER_BINARY_PATH, "--help"], 
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print("✅ Piper TTS kurulu ve hazır")
            else:
                print("❌ Piper binary çalışmıyor, çalıştırma izni verin:")
                print(f"  chmod +x {PIPER_BINARY_PATH}")
                raise Exception("Piper binary çalışmıyor")
        except Exception as e:
            print(f"❌ Piper kontrol hatası: {e}")
            raise
    
    def speak(self, text, wait=True, speed=1.0):
        """Metni Piper TTS ile seslendir - SUBPROCESS İLE"""
        try:
            # Türkçe metni hazırla
            text = self.prepare_turkish_text(text)
            
            # Geçici WAV dosyası oluştur
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                wav_path = tmp_file.name
            
            # Piper komutunu oluştur 
            # Hız ayarı için --length_scale kullanılır (1.0 normal, küçük = hızlı, büyük = yavaş)
            length_scale = 1.0 / speed  # speed > 1 ise daha hızlı
            
            # SADECE SUBPROCESS İLE ECHO KULLAN 
            cmd = f'echo "{text}" | {PIPER_BINARY_PATH} --model {PIPER_MODEL_PATH} --output_file {wav_path} --length_scale {length_scale}'
            
            print(f"🔊 Piper TTS: '{text[:50]}...' (hız: {speed})")
            
            # Komutu çalıştır
            result = subprocess.run(cmd, shell=True, 
                                   capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                print(f"❌ Piper hatası: {result.stderr}")
                return
            
            # WAV dosyasını aplay ile çal
            self.play_wav_with_aplay(wav_path)
            
            # Dosyayı temizle
            if os.path.exists(wav_path):
                os.remove(wav_path)
                
        except subprocess.TimeoutExpired:
            print("❌ Piper zaman aşımı")
        except Exception as e:
            print(f"❌ Piper seslendirme hatası: {e}")
            # Hata durumunda sessiz bekle
            if wait:
                time.sleep(len(text) / (15 * speed))
    
    def play_wav_with_aplay(self, wav_path):
        if not os.path.exists(wav_path):
            return
        
        try:
            # aplay komutu ile WAV dosyasını çal
            subprocess.run(['aplay', '-q', wav_path], 
                          capture_output=True, timeout=10)
        except Exception as e:
            print(f"❌ Ses çalma hatası: {e}")
            # Alternatif: cat ile raw audio
            try:
                subprocess.run(['cat', wav_path, '>', '/dev/dsp'], 
                              shell=True, timeout=5)
            except:
                pass
    
    def speak_async(self, text, speed=1.0):
        """Asenkron seslendirme"""
        Thread(target=self.speak, args=(text, True, speed), daemon=True).start()
    
    def prepare_turkish_text(self, text):
        """Türkçe metni Piper TTS için hazırla"""
        # Piper Türkçe modeli Türkçe karakterleri destekler
        # Tırnak işaretlerini escape et ve satır sonlarını kaldır
        text = text.replace('"', '\\"').replace('\n', ' ').replace('\r', ' ')
        text = ' '.join(text.split())  # Fazla boşlukları temizle
        return text

# ==================== GPIO AYARLARI ====================
class GPIOPins:
    # Röle Pinleri (6 solenoid için)
    RELAY_PINS = [4, 17, 27, 22, 23, 24]
    
    # Buton Pinleri
    BUTTON_NEXT = 5        # Sonraki kitap
    BUTTON_CONFIRM = 6     # Onay/Seçim
    BUTTON_MODE = 13       # Mod değiştirme
    BUTTON_SPEED_UP = 19   # Hız artırma
    BUTTON_SPEED_DOWN = 26 # Hız azaltma
    BUTTON_UPDATE = 21     # Kitapları güncelle
    
    ALL_BUTTONS = [BUTTON_NEXT, BUTTON_CONFIRM, BUTTON_MODE, 
                   BUTTON_SPEED_UP, BUTTON_SPEED_DOWN, BUTTON_UPDATE]

# ==================== BRAILLE KİTAP OKUYUCU ====================
class BrailleBookReader:
    def __init__(self):
        print("🎵 BRAİLLE KİTAP OKUYUCU - PİPER TTS SÜRÜMÜ")
        print("=" * 50)
        
        # PİPER TTS ses motorunu kur
        print("🔊 PİPER TTS başlatılıyor...")
        self.voice_engine = VoiceEngine()
        
        # GPIO Ayarları
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        try:
            GPIO.cleanup()
            time.sleep(0.3)
        except:
            pass
        
        # Değişkenler
        self.books = []
        self.current_book_index = 0
        self.selected_book = None
        self.current_mode = 0
        self.modes = ["sadece_yazma", "sadece_okuma", "hem_okuma_hem_yazma", "egitim_modu"]
        self.mode_names = ["Sadece Yazma", "Sadece Okuma", "Hem Okuma Hem Yazma", "Braille Eğitimi"]
        
        # HIZ AYARLARI
        self.speech_speed = 1.0    # Ses hızı (1.0 normal)
        self.write_speed = 0.5     # Yazma hızı (0.5 saniye/her karakter)
        self.min_speed = 0.3       # Minimum yazma hızı
        self.max_speed = 1.0       # Maksimum yazma hızı
        
        # Fiziksel solenoid ayarları
        self.solenoid_up_time = 0.1    # Solenoid yukarı çıkma süresi
        self.solenoid_down_time = 0.05 # Solenoid aşağı inme süresi (bekleme)
        
        # Sistem durumu
        self.is_running = True
        self.is_playing = False
        self.is_paused = False
        self.stop_event = Event()
        self.progress_data = {}
        self.current_position = 0
        self.current_text = ""
        
        # Buton takibi
        self.button_states = {}
        self.button_press_start = {}
        self.last_button_time = {}
        self.button_debounce = {}
        for pin in GPIOPins.ALL_BUTTONS:
            self.button_debounce[pin] = 0
        
        self.lock = Lock()
        
        # Dizinleri oluştur
        self.setup_directories()
        
        # GPIO'yu ayarla
        self.setup_gpio()
        
        # Braille haritasını yükle
        self.setup_braille_map()
        
        # İlerlemeyi yükle
        self.load_progress()
        
        # Kitapları yükle (yerelden)
        self.load_local_books()
        
        # Otomatik güncelleme thread'i
        self.update_thread = Thread(target=self.auto_update_check, daemon=True)
        self.update_thread.start()
        
        # Başlangıç mesajı - PİPER TTS İLE
        self.speak("Braille kitap okuyucuya hoş geldiniz.")
        time.sleep(0.5)
        
        if self.books:
            self.speak(f"Kütüphanenizde {len(self.books)} kitap bulunuyor.")
            time.sleep(0.5)
            book_name = self.books[0]['name_tr']
            self.speak(f"İlk kitap: {book_name}")
        else:
            self.speak("Henüz hiç kitap yok. Lütfen güncelle tuşuna basarak kitapları indirin.")
        
        self.speak("İleri tuşu ile kitaplar arasında gezin.")
        time.sleep(0.3)
        self.speak("Onay tuşu ile seçin veya duraklat.")
        time.sleep(0.3)
        self.speak("Mod tuşu ile okuma modunu değiştirin.")
        time.sleep(0.3)
        self.speak("Hız artırma ve azaltma tuşları ile okuma hızını ayarlayın.")
        
        print("✅ PİPER TTS sistemi başlatıldı!")
    
    # ==================== PİPER TTS SES FONKSİYONLARI ====================
    def speak(self, text):
        """Metni PİPER TTS ile seslendir"""
        self.voice_engine.speak(text, wait=True, speed=self.speech_speed)
    
    def speak_async(self, text):
        """Asenkron seslendirme - PİPER TTS"""
        self.voice_engine.speak_async(text, self.speech_speed)
    
    def adjust_speed(self, increase=True):
        """Ses ve yazma hızını ayarla"""
        with self.lock:
            if increase:
                # Ses hızını artır (daha hızlı konuşma)
                self.speech_speed = min(2.0, self.speech_speed + 0.2)
                # Yazma hızını azalt (daha hızlı yazma)
                self.write_speed = max(0.3, self.write_speed - 0.1)
            else:
                # Ses hızını azalt (daha yavaş konuşma)
                self.speech_speed = max(0.5, self.speech_speed - 0.2)
                # Yazma hızını artır (daha yavaş yazma)
                self.write_speed = min(1.0, self.write_speed + 0.1)
            
            speed_text = "hızlı" if self.speech_speed > 1.3 else "normal" if self.speech_speed > 0.8 else "yavaş"
            write_text = "hızlı" if self.write_speed < 0.4 else "normal" if self.write_speed < 0.7 else "yavaş"
            print(f"🔧 Hız ayarı: ses={self.speech_speed:.1f} ({speed_text}), yazma={self.write_speed:.1f}s ({write_text})")
            self.speak(f"Ses hızı {speed_text}, yazma hızı {write_text}")
    
    # ==================== GİTHUB PDF SİSTEMİ ====================
    def setup_directories(self):
        """Gerekli dizinleri oluştur"""
        os.makedirs(LOCAL_BOOKS_DIR, exist_ok=True)
        os.makedirs(f"{LOCAL_BOOKS_DIR}/pdfs", exist_ok=True)
    
    def load_local_books(self):
        """Yerel kitapları yükle"""
        auto_file = f"{LOCAL_BOOKS_DIR}/kitaplar_auto.json"
        
        if os.path.exists(auto_file):
            try:
                with open(auto_file, 'r', encoding='utf-8') as f:
                    self.books = json.load(f)
                print(f"📚 {len(self.books)} kitap yüklendi")
            except Exception as e:
                print(f"Kitaplar yüklenirken hata: {e}")
                self.books = []
        else:
            self.books = []
    
    def scan_github_for_pdfs(self):
        """GitHub'daki PDF'leri tara"""
        print("🌐 GitHub'daki PDF'ler taranıyor...")
        
        try:
            headers = {'User-Agent': 'Braille-Book-Reader'}
            response = requests.get(GITHUB_API_URL, headers=headers, timeout=15)
            
            if response.status_code == 200:
                files = response.json()
                books = []
                
                for file in files:
                    if isinstance(file, dict) and file.get('type') == 'file':
                        filename = file.get('name', '')
                        if filename.lower().endswith('.pdf'):
                            book_name = self.create_book_name(filename)
                            books.append({
                                'filename': filename,
                                'name_tr': book_name,
                                'download_url': file.get('download_url', ''),
                                'size': file.get('size', 0),
                                'sha': file.get('sha', '')[:8]
                            })
                
                print(f"✅ {len(books)} PDF bulundu")
                return books
            else:
                print(f"❌ GitHub API hatası: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ Tarama hatası: {e}")
            return []
    
    def create_book_name(self, filename):
        """Dosya adından kitap adı oluştur"""
        name = filename.replace('.pdf', '').replace('.PDF', '')
        for char in ['_', '-', '.']:
            name = name.replace(char, ' ')
        
        words = []
        for word in name.split():
            if word.lower() in ['ve', 'ile', 'de', 'da', 'ki']:
                words.append(word.lower())
            else:
                words.append(word[0].upper() + word[1:].lower())
        
        result = ' '.join(words)
        return result[:40] if len(result) > 40 else result
    
    def update_library(self, speak_progress=True):
        """Kitaplığı güncelle"""
        if speak_progress:
            self.speak("Kitaplar güncelleniyor.")
        
        github_books = self.scan_github_for_pdfs()
        
        if not github_books:
            if speak_progress:
                self.speak("GitHub'dan kitap listesi alınamadı.")
            return
        
        if speak_progress:
            self.speak(f"{len(github_books)} kitap bulundu.")
        
        new_books = []
        for book in github_books:
            local_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
            if not os.path.exists(local_path):
                new_books.append(book)
        
        if speak_progress and new_books:
            self.speak(f"{len(new_books)} yeni kitap indirilecek.")
        
        success_count = 0
        for book in new_books:
            if self.download_book(book):
                success_count += 1
        
        self.save_book_metadata(github_books)
        self.books = github_books
        
        if speak_progress:
            if success_count > 0:
                self.speak(f"Güncelleme tamamlandı. {success_count} kitap eklendi.")
            else:
                self.speak("Tüm kitaplar güncel.")
    
    def download_book(self, book):
        """Kitabı indir"""
        try:
            response = requests.get(book['download_url'], timeout=60)
            if response.status_code == 200:
                file_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"📥 {book['filename']} indirildi")
                return True
            else:
                print(f"❌ {book['filename']} indirilemedi: {response.status_code}")
        except Exception as e:
            print(f"❌ {book['filename']} indirme hatası: {e}")
        return False
    
    def save_book_metadata(self, books):
        """Metadata'yı kaydet"""
        metadata_path = f"{LOCAL_BOOKS_DIR}/kitaplar_auto.json"
        try:
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(books, f, ensure_ascii=False, indent=2)
            print("📁 Metadata kaydedildi")
        except Exception as e:
            print(f"❌ Metadata kaydetme hatası: {e}")
    
    def auto_update_check(self):
        """Otomatik güncelleme kontrolü"""
        while self.is_running:
            time.sleep(UPDATE_INTERVAL)
            try:
                requests.get("https://api.github.com", timeout=5)
                self.update_library(speak_progress=False)
            except:
                pass
    
    # ==================== GPIO ve BUTON KONTROLÜ ====================
    def setup_gpio(self):
        """GPIO pinlerini ayarla"""
        try:
            # Röle pinleri - LOW = Röle kapalı (solenoid pasif)
            for pin in GPIOPins.RELAY_PINS:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)  # Başlangıçta tüm röleler KAPALI
            
            # Buton pinleri
            for pin in GPIOPins.ALL_BUTTONS:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self.button_states[pin] = GPIO.HIGH
                self.button_press_start[pin] = 0
                self.last_button_time[pin] = time.time()
            
            print("✅ GPIO ayarlandı - Tüm röleler başlangıçta kapalı")
            
        except Exception as e:
            print(f"❌ GPIO hatası: {e}")
    
    def check_buttons(self):
        """Butonları kontrol et - DEBOUNCE ile"""
        current_time = time.time()
        
        for pin in GPIOPins.ALL_BUTTONS:
            try:
                current_state = GPIO.input(pin)
                last_state = self.button_states.get(pin, GPIO.HIGH)
                
                # Debounce kontrolü (50ms)
                if current_time - self.last_button_time[pin] < 0.05:
                    continue
                
                # Buton basıldı
                if current_state == GPIO.LOW and last_state == GPIO.HIGH:
                    self.button_press_start[pin] = current_time
                    self.last_button_time[pin] = current_time
                    self.handle_button_press(pin)
                
                # Buton basılı tutuluyor
                elif current_state == GPIO.LOW and last_state == GPIO.LOW:
                    press_duration = current_time - self.button_press_start[pin]
                    
                    # 2 saniye basılı tutunca BAŞTAN BAŞLAT
                    if press_duration >= 2.0 and pin == GPIOPins.BUTTON_NEXT:
                        if self.is_playing and not self.is_paused:
                            self.handle_long_press(pin, press_duration)
                            self.button_press_start[pin] = current_time
                
                # Buton bırakıldı
                elif current_state == GPIO.HIGH and last_state == GPIO.LOW:
                    self.button_press_start[pin] = 0
                
                self.button_states[pin] = current_state
                
            except Exception as e:
                print(f"Buton kontrol hatası: {e}")
    
    def handle_button_press(self, pin):
        """Kısa basma işleyici"""
        current_time = time.time()
        
        # Double press koruması (300ms)
        if current_time - self.button_debounce[pin] < 0.3:
            return
        
        self.button_debounce[pin] = current_time
        
        with self.lock:
            if pin == GPIOPins.BUTTON_NEXT:
                self.next_book()
            elif pin == GPIOPins.BUTTON_CONFIRM:
                self.confirm_selection()
            elif pin == GPIOPins.BUTTON_MODE:
                self.next_mode()
            elif pin == GPIOPins.BUTTON_SPEED_UP:
                print("⬆️ Hız artırma butonuna basıldı")
                self.adjust_speed(increase=True)
            elif pin == GPIOPins.BUTTON_SPEED_DOWN:
                print("⬇️ Hız azaltma butonuna basıldı")
                self.adjust_speed(increase=False)
            elif pin == GPIOPins.BUTTON_UPDATE:
                self.manual_update()
    
    def handle_long_press(self, pin, duration):
        """Uzun basma işleyici - KİTABI BAŞTAN BAŞLAT"""
        if pin == GPIOPins.BUTTON_NEXT and self.is_playing and not self.is_paused:
            print(f"⏪ Uzun basma ({duration:.1f}s): Kitap baştan başlatılıyor...")
            self.speak("Kitap baştan başlatılıyor")
            
            self.stop_event.set()
            time.sleep(0.2)
            self.stop_event.clear()
            
            # Pozisyonu sıfırla
            self.current_position = 0
            
            # İlerlemeyi kaydet
            if self.selected_book:
                book_key = self.selected_book['filename']
                self.progress_data[book_key] = {
                    'position': 0,
                    'mode': self.current_mode,
                    'timestamp': time.time()
                }
                self.save_progress()
            
            # Yeniden başlat (duraklatma durumunu koru)
            self.start_reading()
    
    def next_book(self):
        """Sonraki kitap"""
        if not self.books:
            self.speak("Henüz kitap yok. Güncelle tuşuna basın.")
            return
        
        self.current_book_index = (self.current_book_index + 1) % len(self.books)
        book = self.books[self.current_book_index]
        self.speak(book['name_tr'])
    
    def confirm_selection(self):
        """Seçimi onayla veya DURAKLAT/DEVAM ET"""
        if not self.books:
            self.speak("Önce kitapları güncelleyin.")
            return
        
        if self.selected_book is None:
            # Kitap seçimi
            self.selected_book = self.books[self.current_book_index]
            book = self.selected_book
            self.speak(f"{book['name_tr']} seçildi. Mod seçmek için mod tuşuna basın.")
        elif self.is_playing:
            # DURAKLAT/DEVAM ET
            self.toggle_pause()
        else:
            # Mod seçimi
            self.speak(f"{self.mode_names[self.current_mode]} seçildi. Başlıyor...")
            time.sleep(0.5)
            self.start_reading()
    
    def toggle_pause(self):
        """Duraklat/Devam et"""
        if not self.is_playing:
            return
        
        self.is_paused = not self.is_paused
        
        if self.is_paused:
            self.speak("Duraklatıldı")
            self.clear_solenoids()  # Duraklatma sırasında röleleri kapat
        else:
            self.speak("Devam ediliyor")
    
    def next_mode(self):
        """Sonraki mod"""
        if self.selected_book is None:
            self.speak("Önce bir kitap seçin.")
            return
        
        self.current_mode = (self.current_mode + 1) % len(self.modes)
        self.speak(self.mode_names[self.current_mode])
    
    def manual_update(self):
        """Manuel güncelleme"""
        Thread(target=self.update_library, args=(True,), daemon=True).start()
    
    # ==================== BRAILLE SİSTEMİ ====================
    def setup_braille_map(self):
        """Braille haritasını yükle"""
        self.braille_map = {
            'a': [1,0,0,0,0,0], 'b': [1,1,0,0,0,0], 'c': [1,0,0,1,0,0],
            'ç': [1,0,0,1,1,0], 'd': [1,0,0,1,1,1], 'e': [1,0,0,0,1,0],
            'f': [1,1,0,1,0,0], 'g': [1,1,0,1,1,0], 'ğ': [1,1,0,1,1,1],
            'h': [1,1,0,0,1,0], 'ı': [0,1,0,1,0,1], 'i': [0,1,0,1,0,0],
            'j': [0,1,0,1,1,0], 'k': [1,0,1,0,0,0], 'l': [1,1,1,0,0,0],
            'm': [1,0,1,1,0,0], 'n': [1,0,1,1,1,0], 'o': [1,0,1,0,1,0],
            'ö': [0,1,1,1,0,1], 'p': [1,1,1,1,0,0], 'r': [1,1,1,1,1,0],
            's': [0,1,1,1,0,0], 'ş': [1,1,1,0,1,1], 't': [0,1,1,1,1,1],
            'u': [1,0,1,0,0,1], 'ü': [0,1,1,1,1,0], 'v': [0,1,1,1,0,1],
            'y': [1,0,1,1,1,1], 'z': [1,0,1,0,1,1],
            ' ': [0,0,0,0,0,0], '.': [0,1,0,0,1,1], ',': [0,1,0,0,0,0],
            '!': [0,1,1,0,1,0], '?': [0,1,1,0,0,1],
            '0': [0,1,0,1,0,1], '1': [1,0,0,0,0,0], '2': [1,1,0,0,0,0],
            '3': [1,0,0,1,0,0], '4': [1,0,0,1,1,0], '5': [1,0,0,0,1,0],
            '6': [1,1,0,1,0,0], '7': [1,1,0,1,1,0], '8': [1,1,0,0,1,0],
            '9': [0,1,0,1,1,0]
        }
    
    def set_solenoids(self, pattern):
        """Solenoidleri ayarla - 1 = HIGH (Aktif), 0 = LOW (Pasif)"""
        for i, state in enumerate(pattern[:6]):
            if i < len(GPIOPins.RELAY_PINS):
                if state == 1:
                    GPIO.output(GPIOPins.RELAY_PINS[i], GPIO.HIGH)
                else:
                    GPIO.output(GPIOPins.RELAY_PINS[i], GPIO.LOW)
    
    def clear_solenoids(self):
        """Tüm solenoidleri KAPAT (LOW)"""
        for pin in GPIOPins.RELAY_PINS:
            GPIO.output(pin, GPIO.LOW)
    
    def write_character_fast(self, char):
        """Bir karakteri FİZİKSEL olarak doğru şekilde yaz"""
        char_lower = char.lower()
        if char_lower in self.braille_map:
            pattern = self.braille_map[char_lower]
            
            # Solenoidleri aktif et
            self.set_solenoids(pattern)
            
            # SOLENOİDLERİN YUKARI ÇIKMASI İÇİN YETERLİ SÜRE BEKLE
            time.sleep(self.solenoid_up_time)
            
            # Karakteri yazma süresi (hıza göre ayarlanır)
            time.sleep(max(0.1, self.write_speed - self.solenoid_up_time))
            
            # Solenoidleri kapat
            self.clear_solenoids()
            
            # SOLENOİDLERİN AŞAĞI İNMESİ İÇİN YETERLİ SÜRE BEKLE
            time.sleep(self.solenoid_down_time)
            
            # Harf arası boşluk
            time.sleep(0.03)
            return True
        elif char == ' ':
            # Boşluk için daha uzun bekle
            self.clear_solenoids()
            time.sleep(self.write_speed * 3)
            return True
        return False
    
    def write_word_fast(self, word):
        """Bir kelimeyi HIZLI yaz"""
        for char in word:
            if self.stop_event.is_set() or not self.is_playing or self.is_paused:
                return False
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            if not self.write_character_fast(char):
                # Bilinmeyen karakter için boşluk
                time.sleep(self.write_speed)
        
        return True
    
    # ==================== PDF OKUMA ====================
    def read_pdf_content(self, book):
        """PDF içeriğini oku"""
        pdf_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
        
        if not os.path.exists(pdf_path):
            return ""
        
        try:
            # pdftotext kontrolü
            result = subprocess.run(['which', 'pdftotext'], 
                                   capture_output=True, 
                                   text=True)
            if result.returncode != 0:
                print("⚠️ pdftotext bulunamadı, kuruluyor...")
                subprocess.run(['sudo', 'apt', 'install', '-y', 'poppler-utils'], 
                              stdout=subprocess.DEVNULL, 
                              stderr=subprocess.DEVNULL)
            
            temp_file = "/tmp/kitap_temp.txt"
            cmd = ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, temp_file]
            subprocess.run(cmd, capture_output=True, text=True)
            
            if os.path.exists(temp_file):
                with open(temp_file, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                os.remove(temp_file)
                
                # Metni temizle
                text = ' '.join(text.split())
                return text
            return ""
        except Exception as e:
            print(f"PDF okuma hatası: {e}")
            return ""
    
    def start_reading(self):
        """Okumaya başla"""
        if not self.selected_book:
            return
        
        # Her okuma başlamadan önce solenoidleri kapat
        self.clear_solenoids()
        
        self.stop_event.set()
        self.is_playing = False
        self.is_paused = False
        time.sleep(0.3)
        self.stop_event.clear()
        
        self.speak("Kitap yükleniyor.")
        self.current_text = self.read_pdf_content(self.selected_book)
        
        if not self.current_text or len(self.current_text) < 10:
            self.speak("Kitap okunamadı veya boş.")
            return
        
        book_key = self.selected_book['filename']
        if book_key in self.progress_data:
            self.current_position = self.progress_data[book_key]['position']
            if self.current_position > 0:
                percent_complete = (self.current_position / len(self.current_text)) * 100
                self.speak(f"Kitap yüklendi. Yüzde {int(percent_complete)} tamamlanmış. Kayıtlı yerden devam ediliyor.")
            else:
                self.speak("Kitap baştan başlatılıyor.")
        else:
            self.current_position = 0
        
        self.is_playing = True
        
        # Başlamadan önce tekrar solenoidleri kontrol et
        self.clear_solenoids()
        
        if self.modes[self.current_mode] == "sadece_yazma":
            self.mode_write_only()
        elif self.modes[self.current_mode] == "sadece_okuma":
            self.mode_read_only()
        elif self.modes[self.current_mode] == "hem_okuma_hem_yazma":
            self.mode_read_and_write()
        elif self.modes[self.current_mode] == "egitim_modu":
            self.mode_education()
    
    def mode_write_only(self):
        """Sadece yazma modu - TÜM KİTAP"""
        self.speak("Sadece yazma modu başlıyor. Kitabın tamamı yazılacak.")
        time.sleep(0.5)
        
        total_chars = len(self.current_text)
        char_count = 0
        
        while self.current_position < total_chars and self.is_playing:
            if self.stop_event.is_set():
                break
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            char = self.current_text[self.current_position]
            
            if self.write_character_fast(char):
                char_count += 1
                self.current_position += 1
            
            # Her 100 karakterde bir ilerlemeyi kaydet
            if char_count % 100 == 0:
                self.save_progress()
                # İlerlemeyi sesli bildir (isteğe bağlı)
                percent_complete = (self.current_position / total_chars) * 100
                if percent_complete % 10 == 0:  # Her %10'da bir bildir
                    self.speak_async(f"Yüzde {int(percent_complete)} tamamlandı")
        
        self.is_playing = False
        self.clear_solenoids()  # Mod bittiğinde solenoidleri kapat
        self.save_progress()
        
        if self.current_position >= total_chars:
            self.speak("Kitabın tamamı yazıldı. Tebrikler!")
            # Kitabı tamamladık, pozisyonu sıfırla
            self.current_position = 0
            if self.selected_book:
                book_key = self.selected_book['filename']
                self.progress_data[book_key] = {'position': 0, 'timestamp': time.time()}
                self.save_progress()
        else:
            self.speak("Yazma durduruldu.")
    
    def mode_read_only(self):
        """Sadece okuma modu - TÜM KİTAP"""
        self.speak("Okuma modu başlıyor. Kitabın tamamı okunacak.")
        time.sleep(0.3)
        
        total_chars = len(self.current_text)
        read_position = self.current_position
        
        # Okuma işlemi için bloklar halinde okuma
        while read_position < total_chars and self.is_playing:
            if self.stop_event.is_set():
                break
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            # 1000 karakterlik bloklar halinde oku (ama sınır yok, kitap bitene kadar)
            remaining_text = self.current_text[read_position:]
            
            # Anlamlı bir noktada kesmek için cümle sonu arayalım
            chunk_size = 2000  # Daha büyük bloklar
            text_chunk = remaining_text[:chunk_size]
            
            # Cümle sonu bul
            sentence_end = max(text_chunk.rfind('.'), text_chunk.rfind('!'), text_chunk.rfind('?'))
            if sentence_end > 500:  # En az 500 karakter olsun
                text_chunk = text_chunk[:sentence_end + 1]
            
            if text_chunk.strip():
                self.speak(text_chunk)
            
            read_position += len(text_chunk)
            self.current_position = read_position
            
            # Her 5000 karakterde bir ilerlemeyi kaydet
            if read_position % 5000 < len(text_chunk):
                self.save_progress()
                percent_complete = (read_position / total_chars) * 100
                if percent_complete % 10 == 0:  # Her %10'da bir bildir
                    self.speak_async(f"Yüzde {int(percent_complete)} tamamlandı")
            
            # Kısa bekleme
            time.sleep(0.1)
        
        self.is_playing = False
        self.clear_solenoids()  # Mod bittiğinde solenoidleri kapat
        self.save_progress()
        
        if read_position >= total_chars:
            self.speak("Kitabın tamamı okundu. Tebrikler!")
            # Kitabı tamamladık, pozisyonu sıfırla
            self.current_position = 0
            if self.selected_book:
                book_key = self.selected_book['filename']
                self.progress_data[book_key] = {'position': 0, 'timestamp': time.time()}
                self.save_progress()
        else:
            self.speak("Okuma durduruldu.")
    
    def mode_read_and_write(self):
        """Hem okuma hem yazma modu - TÜM KİTAP"""
        self.speak("Okuma ve yazma modu başlıyor. Kitabın tamamı okunup yazılacak.")
        time.sleep(0.3)
        
        total_chars = len(self.current_text)
        
        while self.current_position < total_chars and self.is_playing:
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            # Mevcut pozisyondan 200 karakterlik bir blok al
            remaining_text = self.current_text[self.current_position:]
            text_chunk = remaining_text[:200]
            
            if not text_chunk.strip():
                break
            
            # Cümle veya kelime sınırı bul
            words = text_chunk.split()
            
            for word in words:
                if self.stop_event.is_set() or not self.is_playing or self.current_position >= total_chars:
                    break
                
                # Duraklatma kontrolü
                while self.is_paused and self.is_playing and not self.stop_event.is_set():
                    time.sleep(0.1)
                
                # Kelimeyi yaz
                if self.write_word_fast(word):
                    # Kelimeyi OKU (asenkron olarak)
                    self.speak_async(word)
                    
                    # Boşluk yaz (sessiz)
                    self.clear_solenoids()
                    time.sleep(self.write_speed * 1.5)
                
                # Pozisyonu güncelle
                self.current_position += len(word) + 1  # +1 for space
                
                # Her 500 karakterde bir kaydet
                if self.current_position % 500 < len(word):
                    self.save_progress()
                    percent_complete = (self.current_position / total_chars) * 100
                    if percent_complete % 10 == 0:  # Her %10'da bir bildir
                        self.speak_async(f"Yüzde {int(percent_complete)} tamamlandı")
            
            # Kısa bekleme
            time.sleep(0.05)
        
        # MOD BİTİŞİ
        if not self.is_paused:
            self.is_playing = False
            self.clear_solenoids()  # Mod bittiğinde solenoidleri kapat
            self.save_progress()
            
            if self.current_position >= total_chars:
                self.speak("Kitabın tamamı okunup yazıldı. Tebrikler!")
                # Kitabı tamamladık, pozisyonu sıfırla
                self.current_position = 0
                if self.selected_book:
                    book_key = self.selected_book['filename']
                    self.progress_data[book_key] = {'position': 0, 'timestamp': time.time()}
                    self.save_progress()
            else:
                self.speak("Okuma modu durduruldu. Devam etmek için onay tuşuna basın.")
    
    def mode_education(self):
        """Braille eğitim modu - TÜM ALFABE"""
        self.speak("Braille eğitim modu başlıyor. Tüm alfabe öğretilecek.")
        time.sleep(0.5)
        
        # Tüm harfleri ve rakamları içeren liste
        letters = [
            ("a", "a harfi"), ("b", "b harfi"), ("c", "c harfi"),
            ("ç", "ç harfi"), ("d", "d harfi"), ("e", "e harfi"),
            ("f", "f harfi"), ("g", "g harfi"), ("ğ", "ğ harfi"),
            ("h", "h harfi"), ("ı", "ı harfi"), ("i", "i harfi"),
            ("j", "j harfi"), ("k", "k harfi"), ("l", "l harfi"),
            ("m", "m harfi"), ("n", "n harfi"), ("o", "o harfi"),
            ("ö", "ö harfi"), ("p", "p harfi"), ("r", "r harfi"),
            ("s", "s harfi"), ("ş", "ş harfi"), ("t", "t harfi"),
            ("u", "u harfi"), ("ü", "ü harfi"), ("v", "v harfi"),
            ("y", "y harfi"), ("z", "z harfi")
        ]
        
        numbers = [
            ("0", "sıfır"), ("1", "bir"), ("2", "iki"),
            ("3", "üç"), ("4", "dört"), ("5", "beş"),
            ("6", "altı"), ("7", "yedi"), ("8", "sekiz"),
            ("9", "dokuz")
        ]
        
        punctuation = [
            (".", "nokta"), (",", "virgül"), 
            ("!", "ünlem işareti"), ("?", "soru işareti")
        ]
        
        # Harfleri öğret
        self.speak("Şimdi harfleri öğrenelim.")
        for char, description in letters:
            if self.stop_event.is_set() or not self.is_playing:
                break
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            self.speak(description)
            time.sleep(0.3)
            
            if char in self.braille_map:
                self.set_solenoids(self.braille_map[char])
                time.sleep(1.5)
                self.clear_solenoids()  # Her harften sonra solenoidleri kapat
                time.sleep(0.3)
        
        if self.stop_event.is_set() or not self.is_playing:
            self.is_playing = False
            self.clear_solenoids()  # Durdurulduğunda solenoidleri kapat
            self.speak("Eğitim durduruldu.")
            return
        
        # Rakamları öğret
        self.speak("Şimdi rakamları öğrenelim.")
        for char, description in numbers:
            if self.stop_event.is_set() or not self.is_playing:
                break
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            self.speak(description)
            time.sleep(0.3)
            
            if char in self.braille_map:
                self.set_solenoids(self.braille_map[char])
                time.sleep(1.5)
                self.clear_solenoids()  # Her rakamdan sonra solenoidleri kapat
                time.sleep(0.3)
        
        if self.stop_event.is_set() or not self.is_playing:
            self.is_playing = False
            self.clear_solenoids()  # Durdurulduğunda solenoidleri kapat
            self.speak("Eğitim durduruldu.")
            return
        
        # Noktalama işaretleri
        self.speak("Şimdi noktalama işaretlerini öğrenelim.")
        for char, description in punctuation:
            if self.stop_event.is_set() or not self.is_playing:
                break
            
            # Duraklatma kontrolü
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.1)
            
            self.speak(description)
            time.sleep(0.3)
            
            if char in self.braille_map:
                self.set_solenoids(self.braille_map[char])
                time.sleep(1.5)
                self.clear_solenoids()  # Her işaretten sonra solenoidleri kapat
                time.sleep(0.3)
        
        self.is_playing = False
        self.clear_solenoids()  # Mod bittiğinde solenoidleri kapat
        self.speak("Braille eğitimi tamamlandı. Tüm harfleri, rakamları ve noktalama işaretlerini öğrendiniz.")
    
    # ==================== İLERLEME YÖNETİMİ ====================
    def load_progress(self):
        """İlerlemeyi yükle"""
        progress_file = f"{LOCAL_BOOKS_DIR}/progress.json"
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    self.progress_data = json.load(f)
                print("📈 İlerleme yüklendi")
            except:
                self.progress_data = {}
    
    def save_progress(self):
        """İlerlemeyi kaydet"""
        if not self.selected_book:
            return
        
        try:
            book_key = self.selected_book['filename']
            self.progress_data[book_key] = {
                'position': self.current_position,
                'mode': self.current_mode,
                'timestamp': time.time()
            }
            
            progress_file = f"{LOCAL_BOOKS_DIR}/progress.json"
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.progress_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ İlerleme kaydetme hatası: {e}")
    
    # ==================== ANA DÖNGÜ ====================
    def main_loop(self):
        """Ana program döngüsü"""
        try:
            while self.is_running:
                self.check_buttons()
                time.sleep(0.02)  # Hızlı kontrol
                
        except KeyboardInterrupt:
            print("\n⏹️ Durduruldu")
            self.cleanup()
        except Exception as e:
            print(f"Hata: {e}")
            self.cleanup()
    
    def cleanup(self):
        """Temizlik"""
        self.is_running = False
        self.stop_event.set()
        self.is_playing = False
        
        time.sleep(0.3)
        self.clear_solenoids()  # Kapanmadan önce solenoidleri kapat
        self.save_progress()
        GPIO.cleanup()
        print("✅ Sistem kapatıldı")

# ==================== ANA PROGRAM ====================
def main():
    """Ana fonksiyon"""
    print("=" * 60)
    print("🎵 BRAİLLE KİTAP OKUYUCU - PİPER TTS SÜRÜMÜ")
    print("=" * 60)
    print("🎯 ÖZELLİKLER:")
    print("  • Fiziksel solenoid kontrolü iyileştirildi")
    print("  • Hız kontrolü butonları düzeltildi")
    print("  • Debounce eklenerek buton tepkisi iyileştirildi")
    print("  • Solenoidler için yukarı çıkma/aşağı inme süreleri eklendi")
    print("  • Röleler sadece yazarken aktif")
    print("=" * 60)
    
    # Bağımlılıkları kontrol et
    try:
        import requests
        import RPi.GPIO
        print("✅ Temel Python paketleri yüklü")
    except ImportError as e:
        print(f"❌ Eksik paket: {e}")
        print("Kurulum için: pip install requests RPi.GPIO")
        return
    
    # Programı başlat
    reader = BrailleBookReader()
    
    try:
        reader.main_loop()
    except Exception as e:
        print(f"Hata: {e}")
        reader.cleanup()

if __name__ == "__main__":
    main()
