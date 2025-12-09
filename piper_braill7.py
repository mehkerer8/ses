#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import queue
import requests
import subprocess
from threading import Thread, Lock, Event
from queue import Queue
import RPi.GPIO as GPIO
import tempfile

# ==================== KONFİGÜRASYON ====================
GITHUB_REPO = "mehkerer8/pdfs"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
LOCAL_BOOKS_DIR = "/home/pixel/braille_books"
UPDATE_INTERVAL = 3600

# PIPER TTS AYARLARI
PIPER_BINARY_PATH = "/home/pixel/braille_project/piper/piper"
PIPER_MODEL_PATH = "/home/pixel/braille_project/piper/tr_TR-fettah-medium.onnx"

# ==================== PİPER TTS SES SİSTEMİ ====================
class VoiceEngine:
    """Piper TTS'i subprocess ile kullanır - HIZLI VERSİYON"""
    
    def __init__(self):
        self.speech_queue = Queue()
        self.is_playing = False
        self.current_process = None
        self.stop_speech = Event()
        self.lock = Lock()
        self.setup()
        # Arka plan thread'ini başlat
        self.speech_thread = Thread(target=self._speech_worker, daemon=True)
        self.speech_thread.start()
    
    def setup(self):
        """Piper TTS sistemini kur"""
        print("🔊 Piper TTS sistemi kuruluyor...")
        
        # Piper binary kontrolü
        if not os.path.exists(PIPER_BINARY_PATH):
            print(f"❌ Piper binary bulunamadı: {PIPER_BINARY_PATH}")
            print("Lütfen şu komutlarla indirin:")
            print("cd ~/braille_project/piper")
            print("wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64 -O piper")
            print("chmod +x piper")
            return
        
        # Model kontrolü
        if not os.path.exists(PIPER_MODEL_PATH):
            print(f"⚠️ Piper modeli bulunamadı: {PIPER_MODEL_PATH}")
            print("Lütfen şu komutla indirin:")
            print("cd ~/braille_project/piper")
            print("wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
            return
        
        print(f"✅ Piper TTS kurulu: {PIPER_BINARY_PATH}")
        print(f"✅ Model: {PIPER_MODEL_PATH}")
        
        # Piper testi
        try:
            test_cmd = [PIPER_BINARY_PATH, '--version']
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print(f"✅ Piper versiyonu: {result.stdout.strip()}")
            else:
                print(f"⚠️ Piper testi başarısız: {result.stderr[:100]}")
        except Exception as e:
            print(f"⚠️ Piper test hatası: {e}")
    
    def _speech_worker(self):
        """Arka planda ses kuyruğunu işler"""
        while not self.stop_speech.is_set():
            try:
                item = self.speech_queue.get(timeout=0.1)
                if item is None:
                    break
                
                text, speed, callback = item
                
                with self.lock:
                    self.is_playing = True
                
                # Piper'ı çalıştır
                self._run_piper_sync(text, speed)
                
                with self.lock:
                    self.is_playing = False
                
                if callback:
                    callback()
                    
                self.speech_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"❌ Ses kuyruğu hatası: {e}")
                with self.lock:
                    self.is_playing = False
    
    def _run_piper_sync(self, text, speed):
        """Piper'ı senkron çalıştır - HIZLI VERSİYON"""
        try:
            # Metni temizle
            text = self._clean_text(text)
            
            if not text.strip():
                return
            
            print(f"🔊 Seslendiriliyor ({speed}x): {text[:50]}...")
            
            # Geçici WAV dosyası oluştur
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                wav_path = tmp_file.name
            
            # HIZ AYARI: speed=1.5 için length_scale=0.67 (daha hızlı)
            length_scale = max(0.4, 1.0 / speed)  # Minimum 0.4, çok hızlı
            sentence_silence = max(0.01, 0.03 / speed)  # Daha kısa sessizlik
            phoneme_silence = max(0.002, 0.005 / speed)  # Daha kısa fonem sessizliği
            
            # HIZLI PİPER KOMUTU
            cmd = [
                PIPER_BINARY_PATH,
                '--model', PIPER_MODEL_PATH,
                '--output_file', wav_path,
                '--length_scale', str(length_scale),
                '--noise_scale', '0.6',      # Daha az gürültü
                '--noise_w', '0.7',          # Daha az varyasyon
                '--sentence_silence', str(sentence_silence),
                '--phoneme_silence', str(phoneme_silence),
                '--quiet'                    # Gereksiz çıktıları bastır
            ]
            
            # Komutu çalıştır
            process = subprocess.run(
                cmd,
                input=text.encode('utf-8'),
                capture_output=True,
                timeout=10  # Daha kısa timeout
            )
            
            if process.returncode != 0:
                print(f"❌ Piper hatası: {process.stderr[:200]}")
                return
            
            # WAV dosyasını hızlı çal
            self._play_wav_fast(wav_path)
            
            # Dosyayı temizle
            if os.path.exists(wav_path):
                os.remove(wav_path)
                
        except subprocess.TimeoutExpired:
            print("⚠️ Piper zaman aşımı, devam ediliyor...")
        except Exception as e:
            print(f"❌ Piper hatası: {e}")
    
    def _play_wav_fast(self, wav_path):
        """WAV dosyasını hızlı çal"""
        if not os.path.exists(wav_path):
            return
        
        try:
            # aplay ile hızlı çal
            subprocess.run(
                ['aplay', '-q', wav_path],
                capture_output=True,
                timeout=5
            )
        except:
            pass
    
    def _clean_text(self, text):
        """Metni temizle ve optimize et"""
        if not text:
            return ""
        
        # Özel karakterleri kaldır
        text = text.replace('"', '').replace("'", "").replace("`", "").replace("´", "")
        
        # Fazla boşlukları temizle
        text = ' '.join(text.split())
        
        # Çok uzun metinleri kısalt
        if len(text) > 300:
            text = text[:297] + "..."
        
        return text.strip()
    
    def speak(self, text, wait=False, speed=1.0, callback=None):
        """Metni seslendir - ASENKRON"""
        if not text or not text.strip():
            return
        
        self.speech_queue.put((text, speed, callback))
    
    def speak_sync(self, text, speed=1.0):
        """Metni senkron seslendir"""
        self._run_piper_sync(text, speed)
    
    def speak_async(self, text, speed=1.0):
        """Asenkron seslendirme"""
        Thread(target=self._run_piper_sync, args=(text, speed), daemon=True).start()
    
    def stop(self):
        """Seslendirmeyi durdur"""
        self.stop_speech.set()
        with self.lock:
            if self.current_process:
                try:
                    self.current_process.terminate()
                except:
                    pass

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
        print("🎵 BRAİLLE KİTAP OKUYUCU - HIZLI PİPER TTS")
        print("=" * 50)
        
        # PİPER TTS ses motorunu kur
        print("🔊 PİPER TTS başlatılıyor...")
        self.voice_engine = VoiceEngine()
        
        # GPIO Ayarları
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        try:
            GPIO.cleanup()
            time.sleep(0.1)
        except:
            pass
        
        # Değişkenler
        self.books = []
        self.current_book_index = 0
        self.selected_book = None
        self.current_mode = 0
        self.modes = ["sadece_yazma", "sadece_okuma", "hem_okuma_hem_yazma", "egitim_modu"]
        self.mode_names = ["Sadece Yazma", "Sadece Okuma", "Hem Okuma Hem Yazma", "Braille Eğitimi"]
        
        # HIZ AYARLARI - DAHA HIZLI
        self.speech_speed = 2.0    # BAŞLANGIÇ HIZI: 2x (eski: 1.5x)
        self.write_speed = 0.15    # Yazma hızı daha hızlı
        self.min_speed = 1.0       # Minimum hız: 1x
        self.max_speed = 4.0       # Maximum hız: 4x
        
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
        self.lock = Lock()
        
        # Dizinleri oluştur
        self.setup_directories()
        
        # GPIO'yu ayarla
        self.setup_gpio()
        
        # Braille haritasını yükle
        self.setup_braille_map()
        
        # İlerlemeyi yükle
        self.load_progress()
        
        # Kitapları yükle
        self.load_local_books()
        
        # Başlangıç mesajı - HIZLI
        print("🎯 Başlangıç mesajı seslendiriliyor...")
        self.speak_sync("Sistem hazır", speed=2.0)
        
        print("✅ Sistem başlatıldı!")
    
    def setup_directories(self):
        """Gerekli dizinleri oluştur"""
        os.makedirs(LOCAL_BOOKS_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(PIPER_BINARY_PATH), exist_ok=True)
    
    def setup_gpio(self):
        """GPIO pinlerini ayarla"""
        print("🔌 GPIO ayarlanıyor...")
        
        # Röle pinlerini çıkış olarak ayarla
        for pin in GPIOPins.RELAY_PINS:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
        
        # Buton pinlerini giriş olarak ayarla
        for pin in GPIOPins.ALL_BUTTONS:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self.button_states[pin] = GPIO.input(pin)
        
        print("✅ GPIO ayarlandı")
    
    def setup_braille_map(self):
        """Braille karakter haritasını yükle"""
        self.braille_map = {
            'a': [1, 0, 0, 0, 0, 0], 'b': [1, 1, 0, 0, 0, 0],
            'c': [1, 0, 0, 1, 0, 0], 'd': [1, 0, 0, 1, 1, 0],
            'e': [1, 0, 0, 0, 1, 0], 'f': [1, 1, 0, 1, 0, 0],
            'g': [1, 1, 0, 1, 1, 0], 'h': [1, 1, 0, 0, 1, 0],
            'i': [0, 1, 0, 1, 0, 0], 'j': [0, 1, 0, 1, 1, 0],
            'k': [1, 0, 1, 0, 0, 0], 'l': [1, 1, 1, 0, 0, 0],
            'm': [1, 0, 1, 1, 0, 0], 'n': [1, 0, 1, 1, 1, 0],
            'o': [1, 0, 1, 0, 1, 0], 'p': [1, 1, 1, 1, 0, 0],
            'q': [1, 1, 1, 1, 1, 0], 'r': [1, 1, 1, 0, 1, 0],
            's': [0, 1, 1, 1, 0, 0], 't': [0, 1, 1, 1, 1, 0],
            'u': [1, 0, 1, 0, 0, 1], 'v': [1, 1, 1, 0, 0, 1],
            'w': [0, 1, 0, 1, 1, 1], 'x': [1, 0, 1, 1, 0, 1],
            'y': [1, 0, 1, 1, 1, 1], 'z': [1, 0, 1, 0, 1, 1],
            ' ': [0, 0, 0, 0, 0, 0], ',': [0, 1, 0, 0, 0, 0],
            '.': [0, 1, 0, 0, 1, 1], '?': [0, 1, 1, 0, 0, 1],
            '!': [0, 1, 1, 0, 1, 0], ':': [0, 1, 0, 0, 1, 0],
            ';': [0, 1, 1, 0, 0, 0], '-': [0, 0, 0, 0, 1, 1],
            '0': [0, 1, 0, 1, 1, 1], '1': [1, 0, 0, 0, 0, 0],
            '2': [1, 1, 0, 0, 0, 0], '3': [1, 0, 0, 1, 0, 0],
            '4': [1, 0, 0, 1, 1, 0], '5': [1, 0, 0, 0, 1, 0],
            '6': [1, 1, 0, 1, 0, 0], '7': [1, 1, 0, 1, 1, 0],
            '8': [1, 1, 0, 0, 1, 0], '9': [0, 1, 0, 1, 0, 0]
        }
        print(f"✅ {len(self.braille_map)} Braille karakteri yüklendi")
    
    def load_progress(self):
        """İlerleme verilerini yükle"""
        progress_file = os.path.join(LOCAL_BOOKS_DIR, "progress.json")
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    self.progress_data = json.load(f)
                print(f"✅ İlerleme yüklendi: {len(self.progress_data)} kitap")
            except:
                self.progress_data = {}
        else:
            self.progress_data = {}
    
    def save_progress(self):
        """İlerleme verilerini kaydet"""
        progress_file = os.path.join(LOCAL_BOOKS_DIR, "progress.json")
        try:
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.progress_data, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def load_local_books(self):
        """Yerel kitapları yükle"""
        print("📚 Kitaplar yükleniyor...")
        self.books = []
        
        if os.path.exists(LOCAL_BOOKS_DIR):
            for file in os.listdir(LOCAL_BOOKS_DIR):
                if file.endswith('.txt'):
                    book_path = os.path.join(LOCAL_BOOKS_DIR, file)
                    book_name = os.path.splitext(file)[0]
                    
                    self.books.append({
                        'name': book_name,
                        'path': book_path,
                        'type': 'local'
                    })
        
        if self.books:
            print(f"✅ {len(self.books)} kitap yüklendi")
            for i, book in enumerate(self.books[:5]):
                print(f"  {i+1}. {book['name']}")
        else:
            print("📝 Örnek kitap oluşturuluyor...")
            example_path = os.path.join(LOCAL_BOOKS_DIR, "ornek_kitap.txt")
            with open(example_path, 'w', encoding='utf-8') as f:
                f.write("Merhaba, bu bir örnek kitaptır.\n")
                f.write("Braille kitap okuyucu testi.\n")
                f.write("Bu sistem körler için kitap okumayı kolaylaştırır.\n")
                f.write("Sesli okuma ve Braille yazma özellikleri mevcuttur.\n")
            
            self.books.append({
                'name': "ornek_kitap",
                'path': example_path,
                'type': 'local'
            })
            print("✅ Örnek kitap oluşturuldu")
    
    def speak(self, text, speed=None):
        """Metni seslendir"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak(text, speed=speed)
    
    def speak_sync(self, text, speed=None):
        """Senkron seslendirme"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak_sync(text, speed)
    
    def speak_async(self, text, speed=None):
        """Asenkron seslendirme"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak_async(text, speed)
    
    def adjust_speed(self, increase=True):
        """Ses hızını ayarla - DAHA BÜYÜK ADIMLAR"""
        with self.lock:
            if increase:
                self.speech_speed = min(self.max_speed, self.speech_speed + 0.5)
                self.write_speed = max(0.1, self.write_speed - 0.03)
            else:
                self.speech_speed = max(self.min_speed, self.speech_speed - 0.5)
                self.write_speed = min(0.25, self.write_speed + 0.03)
            
            # Hızlı geri bildirim
            if self.speech_speed >= 3.0:
                speed_text = "çok hızlı"
            elif self.speech_speed >= 2.0:
                speed_text = "hızlı"
            elif self.speech_speed >= 1.5:
                speed_text = "orta"
            else:
                speed_text = "yavaş"
            
            self.speak_async(f"Hız {speed_text}", speed=self.speech_speed * 1.1)
    
    def write_braille(self, text):
        """Metni Braille olarak yaz"""
        if not text:
            return
        
        print(f"✍️ Braille yazılıyor: {text[:20]}...")
        
        for char in text.lower():
            if char in self.braille_map:
                pattern = self.braille_map[char]
                
                # Solenoitleri aktif et
                for i, state in enumerate(pattern):
                    if i < len(GPIOPins.RELAY_PINS):
                        GPIO.output(GPIOPins.RELAY_PINS[i], GPIO.HIGH if state else GPIO.LOW)
                
                # Kısa bekleme
                time.sleep(self.write_speed)
                
                # Solenoitleri kapat
                for pin in GPIOPins.RELAY_PINS:
                    GPIO.output(pin, GPIO.LOW)
                
                # Karakter arası bekleme
                time.sleep(0.02)
            else:
                # Bilinmeyen karakter için boşluk
                time.sleep(self.write_speed * 1.5)
    
    def check_buttons(self):
        """Buton durumlarını kontrol et"""
        current_time = time.time()
        
        for pin in GPIOPins.ALL_BUTTONS:
            current_state = GPIO.input(pin)
            previous_state = self.button_states.get(pin, GPIO.HIGH)
            
            # Buton basıldı (HIGH -> LOW)
            if current_state == GPIO.LOW and previous_state == GPIO.HIGH:
                press_time = current_time
                self.button_press_start[pin] = press_time
                
                # Debounce kontrolü
                last_press = self.last_button_time.get(pin, 0)
                if current_time - last_press > 0.3:  # 300ms debounce
                    self.handle_button_press(pin)
                    self.last_button_time[pin] = current_time
            
            # Buton bırakıldı (LOW -> HIGH)
            elif current_state == GPIO.HIGH and previous_state == GPIO.LOW:
                if pin in self.button_press_start:
                    press_duration = current_time - self.button_press_start[pin]
                    if press_duration > 1.0:  # 1 saniyeden uzun basma
                        self.handle_long_press(pin, press_duration)
                    del self.button_press_start[pin]
            
            # Durumu güncelle
            self.button_states[pin] = current_state
    
    def handle_button_press(self, pin):
        """Kısa basma işlemi"""
        print(f"🔘 Buton {pin} basıldı")
        
        if pin == GPIOPins.BUTTON_NEXT:
            self.next_book()
        elif pin == GPIOPins.BUTTON_CONFIRM:
            self.confirm_selection()
        elif pin == GPIOPins.BUTTON_MODE:
            self.change_mode()
        elif pin == GPIOPins.BUTTON_SPEED_UP:
            self.adjust_speed(increase=True)
        elif pin == GPIOPins.BUTTON_SPEED_DOWN:
            self.adjust_speed(increase=False)
        elif pin == GPIOPins.BUTTON_UPDATE:
            self.update_books()
    
    def handle_long_press(self, pin, duration):
        """Uzun basma işlemi"""
        print(f"🔘 Buton {pin} uzun basıldı ({duration:.1f}s)")
        
        if pin == GPIOPins.BUTTON_CONFIRM:
            if self.selected_book:
                self.speak_async("Kitap durduruluyor")
                self.stop_event.set()
    
    def next_book(self):
        """Sonraki kitaba geç"""
        if not self.books:
            self.speak_async("Kitap bulunamadı")
            return
        
        self.current_book_index = (self.current_book_index + 1) % len(self.books)
        book = self.books[self.current_book_index]
        
        self.speak_async(f"{book['name']}. {self.current_book_index + 1} numara", speed=self.speech_speed)
    
    def confirm_selection(self):
        """Kitap seçimini onayla"""
        if not self.books:
            self.speak_async("Kitap bulunamadı")
            return
        
        self.selected_book = self.books[self.current_book_index]
        self.speak_async(f"{self.selected_book['name']} seçildi. Başlatılıyor", speed=self.speech_speed)
        
        # Seçilen kitabı oku
        time.sleep(0.5)
        self.read_selected_book()
    
    def change_mode(self):
        """Çalışma modunu değiştir"""
        self.current_mode = (self.current_mode + 1) % len(self.modes)
        mode_name = self.mode_names[self.current_mode]
        
        self.speak_async(f"{mode_name} modu", speed=self.speech_speed)
    
    def update_books(self):
        """Kitapları güncelle"""
        self.speak_async("Kitaplar güncelleniyor", speed=self.speech_speed)
        self.load_local_books()
        self.speak_async(f"{len(self.books)} kitap yüklendi", speed=self.speech_speed)
    
    def read_selected_book(self):
        """Seçilen kitabı oku"""
        if not self.selected_book:
            self.speak_async("Kitap seçilmedi")
            return
        
        book_path = self.selected_book['path']
        book_name = self.selected_book['name']
        
        print(f"📖 Kitap okunuyor: {book_name}")
        self.speak_async(f"{book_name} kitabı okunuyor", speed=self.speech_speed)
        
        # İlerleme noktasını yükle
        last_position = self.progress_data.get(book_name, 0)
        if last_position > 0:
            self.speak_async(f"Kaldığın yerden devam ediliyor", speed=self.speech_speed)
        
        try:
            # Kitabı oku
            with open(book_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # İçeriği parçalara ayır
            paragraphs = [p.strip() for p in content.split('\n') if p.strip()]
            
            if not paragraphs:
                self.speak_async("Kitap boş")
                return
            
            # Okuma döngüsü
            self.stop_event.clear()
            self.current_position = last_position
            
            for i in range(self.current_position, len(paragraphs)):
                if self.stop_event.is_set():
                    break
                
                paragraph = paragraphs[i]
                self.current_text = paragraph
                
                print(f"📄 [{i+1}/{len(paragraphs)}] {paragraph[:50]}...")
                
                # Moda göre işlem yap
                if self.modes[self.current_mode] == "sadece_okuma":
                    self.speak_sync(paragraph)
                elif self.modes[self.current_mode] == "sadece_yazma":
                    self.write_braille(paragraph)
                elif self.modes[self.current_mode] == "hem_okuma_hem_yazma":
                    # Paralel çalıştırma
                    Thread(target=self.write_braille, args=(paragraph,), daemon=True).start()
                    self.speak_sync(paragraph)
                elif self.modes[self.current_mode] == "egitim_modu":
                    self.speak_async(paragraph)
                    time.sleep(0.5)
                    self.write_braille(paragraph)
                    time.sleep(0.5)
                    self.speak_async("Tekrar")
                    self.write_braille(paragraph)
                
                # İlerlemeyi kaydet
                self.progress_data[book_name] = i + 1
                self.save_progress()
                
                # Kısa bekleme
                if not self.stop_event.is_set():
                    time.sleep(0.3)
            
            # Kitap bitti
            if not self.stop_event.is_set():
                self.speak_async(f"{book_name} kitabı bitti", speed=self.speech_speed)
                self.progress_data[book_name] = 0
                self.save_progress()
            
        except Exception as e:
            print(f"❌ Kitap okuma hatası: {e}")
            self.speak_async("Kitap okunurken hata oluştu")
    
    def main_loop(self):
        """Ana döngü"""
        print("\n🎮 Ana döngü başlatıldı")
        print("📋 Kontroller:")
        print("  ▶️  Buton 5: Sonraki kitap")
        print("  ✅ Buton 6: Seçimi onayla")
        print("  🔄 Buton 13: Mod değiştir")
        print("  ⬆️  Buton 19: Hız artır")
        print("  ⬇️  Buton 26: Hız azalt")
        print("  🔄 Buton 21: Kitapları güncelle")
        print("=" * 50)
        
        self.speak_async("Kontrol butonları aktif. Bir kitap seçin", speed=self.speech_speed)
        
        while self.is_running:
            try:
                # Butonları kontrol et
                self.check_buttons()
                
                time.sleep(0.05)
                
            except KeyboardInterrupt:
                print("\n⏹️ Durduruldu")
                self.is_running = False
                break
            except Exception as e:
                print(f"⚠️ Ana döngü hatası: {e}")
                time.sleep(1)
    
    def cleanup(self):
        """Temizlik işlemleri"""
        print("🧹 Temizlik yapılıyor...")
        
        self.is_running = False
        self.stop_event.set()
        self.voice_engine.stop()
        
        # GPIO pinlerini temizle
        try:
            for pin in GPIOPins.RELAY_PINS:
                GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup()
            print("✅ GPIO temizlendi")
        except:
            pass
        
        print("✅ Sistem durduruldu")

# ==================== SES SİSTEMİ TESTİ ====================
def test_sound_system():
    """Ses sistemini test et"""
    print("🎵 SES SİSTEMİ TESTİ")
    print("=" * 50)
    
    # Piper kontrolü
    piper_path = "/home/pixel/braille_project/piper/piper"
    model_path = "/home/pixel/braille_project/piper/tr_TR-fettah-medium.onnx"
    
    if not os.path.exists(piper_path):
        print("❌ Piper binary bulunamadı!")
        return False
    
    if not os.path.exists(model_path):
        print("❌ Piper modeli bulunamadı!")
        return False
    
    print("✅ Piper binary ve model bulundu")
    
    # Test komutu
    test_text = "Merhaba, ses sistemi test ediliyor"
    
    try:
        # Geçici WAV dosyası oluştur
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            wav_path = tmp.name
        
        # Piper ile WAV oluştur
        cmd = [
            piper_path,
            '--model', model_path,
            '--output_file', wav_path,
            '--length_scale', '0.67'  # Hızlı konuşma
        ]
        
        process = subprocess.run(
            cmd,
            input=test_text.encode('utf-8'),
            capture_output=True,
            timeout=10
        )
        
        if process.returncode != 0:
            print(f"❌ Piper hatası: {process.stderr[:200]}")
            return False
        
        print(f"✅ WAV oluşturuldu")
        
        # WAV dosyasını kontrol et
        if os.path.exists(wav_path):
            file_size = os.path.getsize(wav_path)
            print(f"📁 WAV boyutu: {file_size} bytes")
            
            if file_size == 0:
                print("❌ WAV dosyası boş!")
                os.remove(wav_path)
                return False
            
            # Ses çalma testi
            print("🔊 Ses çalınıyor...")
            result = subprocess.run(
                ['aplay', '-q', wav_path],
                capture_output=True,
                timeout=5
            )
            
            if result.returncode != 0:
                print(f"❌ aplay hatası")
            else:
                print("✅ Ses başarıyla çalındı!")
            
            # Temizlik
            os.remove(wav_path)
            
            return result.returncode == 0
            
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        return False

# ==================== ANA PROGRAM ====================
def main():
    """Ana fonksiyon"""
    print("=" * 60)
    print("🎵 BRAİLLE KİTAP OKUYUCU - HIZLI PİPER TTS")
    print("=" * 60)
    
    # Önce ses sistemini test et
    print("🔊 Ses sistemi test ediliyor...")
    if not test_sound_system():
        print("❌ Ses sistemi testi başarısız!")
        print("💡 Düzeltmeler:")
        print("  1. Piper binary'sini indirin:")
        print("     cd ~/braille_project/piper")
        print("     wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64 -O piper")
        print("     chmod +x piper")
        print("  2. Modeli indirin:")
        print("     wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
        print("  3. Ses çıkışını ayarlayın:")
        print("     sudo raspi-config")
        print("     -> System Options -> Audio -> 3.5mm jack")
        return
    
    print("✅ Ses sistemi testi başarılı!")
    print("=" * 60)
    
    try:
        import requests
        import RPi.GPIO
        print("✅ Python paketleri yüklü")
    except ImportError as e:
        print(f"❌ Eksik paket: {e}")
        print("Kurulum için: pip install requests RPi.GPIO")
        return
    
    reader = BrailleBookReader()
    
    try:
        reader.main_loop()
    except KeyboardInterrupt:
        print("\n⏹️ Durduruldu")
        reader.cleanup()
    except Exception as e:
        print(f"Hata: {e}")
        reader.cleanup()

if __name__ == "__main__":
    main()
