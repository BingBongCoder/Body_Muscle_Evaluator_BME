import socket
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import threading
import csv
import time
import os
from scipy.signal import welch
from collections import deque
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# =============================================================
# KONFIGURASI SISTEM
# =============================================================
HOST = '0.0.0.0'
PORT = 8080
SAMPLING_FREQ = 2500.0
WINDOW_SIZE = 500
PLOT_WINDOW_WIDTH = 11   
PLOT_WINDOW_HEIGHT = 6   
csv_filename = "semg_data_log.csv"

# Kunci (Lock) untuk mengamankan sinkronisasi variabel antar thread
data_lock = threading.Lock()

# =============================================================
# FUNGSI EKSTRAKSI FITUR
# =============================================================
def calculate_features(data, fs):
    rms = np.sqrt(np.mean(data**2))
    mav = np.mean(np.abs(data))
    wl = np.sum(np.abs(np.diff(data)))
    
    if len(data) > 0:
        zcr = np.sum(np.abs(np.diff(np.sign(data)))) / (2 * len(data))
    else:
        zcr = 0
        
    ssi = np.sum(data**2)
    
    freqs, psd = welch(data, fs=fs, nperseg=min(len(data), 256))
    psd_sum = np.sum(psd)
    
    if psd_sum > 0:
        mnf = np.sum(freqs * psd) / psd_sum
        mdf = freqs[np.where(np.cumsum(psd) >= psd_sum / 2.0)[0][0]]
        peak_freq = freqs[np.argmax(psd)]
    else:
        mnf = 0
        mdf = 0
        peak_freq = 0
        
    return [rms, mav, wl, zcr, ssi, mnf, mdf, peak_freq]

# =============================================================
# THREAD KOMUNIKASI TCP
# =============================================================
def tcp_server_thread():
    global is_running, is_recording, current_label, is_paused
    global raw_signal_buffer, feature_calc_buffer, label_list, raw_signal_window
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)
    
    print(f"[*] Menunggu koneksi dari perangkat BME di port {PORT}...")
    conn, addr = server_socket.accept()
    print(f"[+] Terhubung dengan BME: {addr}")
    
    try:
        conn.sendall(b"MOS:1\n")
        print("[+] Perintah Power ON dikirim.")
    except Exception as e:
        print(f"[-] Gagal mengirim perintah: {e}")
    
    data_residual = ""
    
    while is_running:
        try:
            packet = conn.recv(4096).decode('utf-8', errors='ignore')
            if not packet:
                break
            
            data_residual += packet
            while "\n" in data_residual:
                line, data_residual = data_residual.split("\n", 1)
                line = line.strip()
                
                if line.startswith("E1:"):
                    vals_str = line.split(":", 1)[1]
                    if not vals_str:
                        continue
                        
                    voltages = [float(v) for v in vals_str.split(",") if v]
                    
                    # Terapkan Data Lock agar tidak bentrok dengan tombol Pause/Mode
                    with data_lock:
                        for v in voltages:
                            raw_signal_window.append(v)
                            
                            if is_recording and not is_paused:
                                ts_actual = len(raw_signal_buffer) / SAMPLING_FREQ 
                                
                                # Logika 30 detik ditempatkan di sini agar akurasi pergantian label 100% absolut 
                                if current_label == 0 and ts_actual >= 30.0:
                                    current_label = 1
                                
                                raw_signal_buffer.append(v)
                                feature_calc_buffer.append(v)
                                label_list.append(current_label)
                                
                                if len(feature_calc_buffer) >= WINDOW_SIZE:
                                    try:
                                        window_data = np.array(feature_calc_buffer)
                                        features = calculate_features(window_data, SAMPLING_FREQ)
                                        
                                        with open(csv_filename, 'a', newline='') as f:
                                            writer = csv.writer(f)
                                            row = [subject_name, ts_actual, current_label]
                                            row.extend([f"{feat:.4f}" for feat in features])
                                            writer.writerow(row)
                                    except Exception as ex:
                                        print(f"\n[-] Peringatan: Gagal menyimpan ke CSV (File mungkin dibuka di program lain): {ex}")
                                    
                                    feature_calc_buffer.clear()
                                    
        except Exception as e:
            if is_running:
                print(f"[-] Error koneksi atau pengulangan sirkuit: {e}")
            break
            
    conn.close()
    server_socket.close()

# =============================================================
# FUNGSI PEMBARUAN COUNTER (SINKRON DENGAN BUFFER DATA)
# =============================================================
def update_plot(frame):
    # Hanya membaca nilai dengan aman
    with data_lock:
        local_is_recording = is_recording
        local_is_paused = is_paused
        local_label = current_label
        local_durasi = len(raw_signal_buffer) / SAMPLING_FREQ

    if local_is_recording:
        counter_text.set_text(f"DURASI PEREKAMAN : {local_durasi:.1f} s")
        label_names = {-1: "[ STANDBY ]", 0: "[ BASELINE ]", 1: "[ CONTRACTION ]", 2: "[ FATIGUE ]"}
        pause_status = " (PAUSED)" if local_is_paused else ""
        status_text.set_text(f"STATUS UTAMA: {label_names[local_label]}{pause_status}")
        
    return status_text, counter_text

# =============================================================
# KONTROL LOGIKA TOMBOL ANGKA
# =============================================================
def on_press(event):
    global is_running
    key = event.key if event.key else ''
        
    if key == '1':
        with data_lock:
            if not is_recording:
                globals()['is_recording'] = True
                globals()['is_paused'] = False
                globals()['current_label'] = 0
                raw_signal_buffer.clear()
                label_list.clear()
                feature_calc_buffer.clear()
                print("[*] Perekaman dimulai: Baseline Mode (30 Detik)...")
        
    elif key == '2':
        with data_lock:
            if is_recording:
                globals()['current_label'] = 2
                print("[*] Pindah ke Status: FATIGUE Mode.")
        
    elif key == '3':
        with data_lock:
            globals()['is_paused'] = not is_paused
            paused_state = globals()['is_paused']
        if paused_state:
            print("[*] Perekaman DIHENTIKAN SEMENTARA (Pause).")
        else:
            print("[*] Perekaman DILANJUTKAN (Resume).")
        
    elif key == '4':
        is_running = False
        plt.close()

# =============================================================
# LOOP PROGRAM UTAMA
# =============================================================
while True:
    with data_lock:
        raw_signal_buffer = []
        raw_signal_window = deque(maxlen=WINDOW_SIZE)
        feature_calc_buffer = []
        current_label = -1
        is_recording = False
        is_running = True
        is_paused = False 
        label_list = []

    file_exists = os.path.isfile(csv_filename)

    print("\n=============================================================")
    subject_name = input("Masukkan Nama Subjek: ").strip() or "Unknown"
    print(f"[*] Perekaman                  : {subject_name}")
    print(f"[*] Data fitur akan disimpan ke: {csv_filename}")
    print("=============================================================")

    if not file_exists:
        with open(csv_filename, mode='w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Subject_Name", "Timestamp", "Label_Class", 
                "RMS", "MAV", "WL", "ZCR", "SSI", "MNF", "MDF", "PeakFreq"
            ])

    fig, ax = plt.subplots(figsize=(PLOT_WINDOW_WIDTH, PLOT_WINDOW_HEIGHT))
    ax.axis('off')

    title_text = ax.text(0.5, 0.92, "PEREKAMAN DATA SEMG", 
                          transform=ax.transAxes, fontsize=16, fontweight='bold', ha='center', color='black')
    status_text = ax.text(0.5, 0.65, 'STATUS : [ STANDBY ]', 
                          transform=ax.transAxes, fontsize=20, fontweight='bold', ha='center', color='red')
    counter_text = ax.text(0.5, 0.45, 'DURASI PEREKAMAN : 0.0 s', 
                           transform=ax.transAxes, fontsize=28, fontweight='bold', ha='center', color='blue')

    instr_text = (
        "LANGKAH-LANGKAH PEREKAMAN sEMG:\n\n"
        "1. Tekan [ 1 ] : Memulai Rekaman          = otot rileks selama 30 detik\n"
        "2. Detik 30    : [ CONTRACTION ]            = beban diberikan ke tangan sehingga otot berkontraksi\n"
        "3. Tekan [ 2 ] : [ FATIGUE ]                      = otot mulai lelah ~ tahan 15 detik setelah menekan tombol\n"
        "4. Tekan [ 3 ] : PAUSE / RESUME perekaman data, spektrum, dan timer\n"
        "5. Tekan [ 4 ] : Menghentikan Rekaman = membuat Analisis Spektrum"
    )
             
    ax.text(0.05, 0.04, instr_text, transform=ax.transAxes, fontsize=10.5, 
            ha='left', va='bottom', bbox=dict(facecolor='lightyellow', alpha=0.95, edgecolor='gray', pad=12))

    fig.canvas.mpl_connect('key_press_event', on_press)
    
    tcp_thread = threading.Thread(target=tcp_server_thread, daemon=True)
    tcp_thread.start()

    ani = FuncAnimation(fig, update_plot, interval=50, blit=True, cache_frame_data=False)
    plt.show()

    is_running = False
    tcp_thread.join(timeout=1.0)

    # =============================================================
    # POST-PROCESSING: ANALISIS SPEKTRUM DENGAN SISTEM TAB
    # =============================================================
    print("[*] Perekaman selesai. Melakukan Analisis Spektrum...")

    if len(raw_signal_buffer) > 500:
        sig_arr = np.array(raw_signal_buffer)
        lbl_arr = np.array(label_list)
        
        # Mengekstrak data berdasarkan pelabelan yang sudah akurat
        sig_c = sig_arr[lbl_arr == 1]
        sig_f = sig_arr[lbl_arr == 2]

        def clean_spectrum(f, psd):
            mask = (f >= 0)
            for harmonic in range(50, int(f.max()), 50):
                mask &= ~((f >= harmonic - 2) & (f <= harmonic + 2))
            return f[mask], psd[mask]

        f_c, psd_c, mdf_c = None, None, None
        f_f, psd_f, mdf_f = None, None, None

        if len(sig_c) >= 500:
            nseg_c = min(len(sig_c), 2048)
            f_c, psd_c = welch(sig_c, fs=SAMPLING_FREQ, nperseg=nseg_c)
            f_c, psd_c = clean_spectrum(f_c, psd_c)
            mdf_c = f_c[np.where(np.cumsum(psd_c) >= np.sum(psd_c) / 2.0)[0][0]]
            
        if len(sig_f) >= 500:
            nseg_f = min(len(sig_f), 2048)
            f_f, psd_f = welch(sig_f, fs=SAMPLING_FREQ, nperseg=nseg_f)
            f_f, psd_f = clean_spectrum(f_f, psd_f)
            mdf_f = f_f[np.where(np.cumsum(psd_f) >= np.sum(psd_f) / 2.0)[0][0]]

        fig_spec, ax_spec = plt.subplots(figsize=(15, 7))
        plt.subplots_adjust(top=0.85, bottom=0.15) 

        def render_tab(tab_mode):
            ax_spec.clear() 
            
            if tab_mode == 'CONTRACTION':
                if f_c is not None:
                    ax_spec.plot(f_c, psd_c, color='blue', label='Contraction (Tanpa MDF)')
                ax_spec.set_title("Analisis Spektrum sEMG - Fase Contraction Saja")
                
            elif tab_mode == 'FATIGUE':
                if f_f is not None:
                    ax_spec.plot(f_f, psd_f, color='red', label='Fatigue (Tanpa MDF)')
                ax_spec.set_title("Analisis Spektrum sEMG - Fase Fatigue Saja")
                
            elif tab_mode == 'COMBINED':
                if f_c is not None:
                    ax_spec.plot(f_c, psd_c, color='blue', label=f'Contraction (MDF={mdf_c:.1f}Hz)' if mdf_c else 'Contraction')
                    if mdf_c: ax_spec.axvline(mdf_c, color='blue', linestyle='--')
                if f_f is not None:
                    ax_spec.plot(f_f, psd_f, color='red', label=f'Fatigue (MDF={mdf_f:.1f}Hz)' if mdf_f else 'Fatigue')
                    if mdf_f: ax_spec.axvline(mdf_f, color='red', linestyle='--')
                ax_spec.set_title("Analisis Spektrum sEMG - Gabungan Komplit")

            ax_spec.set_xlabel("Frekuensi (Hz)")
            ax_spec.set_ylabel("Power Spectral Density (PSD)")
            ax_spec.set_xlim(0, 500)
            ax_spec.legend()
            ax_spec.grid(True)
            plt.draw() 

        # Tombol Navigasi (Tabs)
        ax_btn1 = plt.axes([0.15, 0.90, 0.18, 0.05])
        btn_tab1 = Button(ax_btn1, 'Contraction Saja', hovercolor='lightblue')
        btn_tab1.on_clicked(lambda event: render_tab('CONTRACTION'))

        ax_btn2 = plt.axes([0.35, 0.90, 0.18, 0.05])
        btn_tab2 = Button(ax_btn2, 'Fatigue Saja', hovercolor='lightcoral')
        btn_tab2.on_clicked(lambda event: render_tab('FATIGUE'))

        ax_btn3 = plt.axes([0.55, 0.90, 0.18, 0.05])
        btn_tab3 = Button(ax_btn3, 'Gabungan + MDF', hovercolor='lightgray')
        btn_tab3.on_clicked(lambda event: render_tab('COMBINED'))

        def repeat_callback(event):
            print("[*] Mempersiapkan sistem... Mengulangi program ke awal.")
            plt.close(fig_spec) 

        ax_reset = plt.axes([0.80, 0.015, 0.18, 0.05]) 
        btn_repeat = Button(ax_reset, 'ULANGI PROGRAM (RESET)', color='lightgreen', hovercolor='lime')
        btn_repeat.on_clicked(repeat_callback)

        render_tab('COMBINED')
        plt.show()
        
    else:
        print("[-] Data tidak cukup untuk melakukan analisis spektrum.")
        pilihan = input("[?] Data tidak cukup. Ingin ulangi program dari awal? (y/n): ").strip().lower()
        if pilihan != 'y':
            break
