import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import os
import re
import threading
from pathlib import Path


class BinaryStringScanner:
    def __init__(self, root):
        self.root = root
        self.root.title("Binary String Scanner")
        self.root.geometry("900x700")

        # --- State ---
        self.selected_path = tk.StringVar()
        self.scan_mode = tk.StringVar(value="file")   # "file" | "folder"
        self.min_length = tk.IntVar(value=4)
        self.max_strings_per_file = tk.IntVar(value=0)
        self.scanning = False

        # Encoding toggles
        self.scan_ascii   = tk.BooleanVar(value=True)
        self.scan_utf16   = tk.BooleanVar(value=True)

        # Alignment option
        self.align4 = tk.BooleanVar(value=False)

        # Search
        self.search_string         = tk.StringVar()
        self.search_case_sensitive = tk.BooleanVar(value=False)
        self.search_use_regex      = tk.BooleanVar(value=False)
        self.search_only_matches   = tk.BooleanVar(value=False)

        self.setup_ui()

    # ------------------------------------------------------------------ UI --

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # ---- Modus-Auswahl ----
        mode_frame = ttk.LabelFrame(main_frame, text="Modus", padding="5")
        mode_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        ttk.Radiobutton(mode_frame, text="Einzelne Datei", variable=self.scan_mode,
                        value="file",   command=self._update_browse_label).grid(row=0, column=0, padx=(0, 12))
        ttk.Radiobutton(mode_frame, text="Ordner (rekursiv)", variable=self.scan_mode,
                        value="folder", command=self._update_browse_label).grid(row=0, column=1)

        # ---- Pfad ----
        path_frame = ttk.Frame(main_frame)
        path_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="Pfad:").grid(row=0, column=0, sticky="w")
        ttk.Entry(path_frame, textvariable=self.selected_path, width=55).grid(
            row=0, column=1, padx=(5, 5), sticky="ew")
        self.browse_btn = ttk.Button(path_frame, text="Datei wählen", command=self.select_path)
        self.browse_btn.grid(row=0, column=2)

        # ---- Einstellungen ----
        settings_frame = ttk.LabelFrame(main_frame, text="Einstellungen", padding="5")
        settings_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        ttk.Label(settings_frame, text="Min. Länge:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(settings_frame, from_=1, to=64, textvariable=self.min_length,
                    width=6).grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(settings_frame, text="Max. Strings/Datei:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(settings_frame, from_=0, to=100000, textvariable=self.max_strings_per_file,
                    width=8).grid(row=0, column=3, sticky="w", padx=(4, 4))
        ttk.Label(settings_frame, text="(0 = alle)").grid(row=0, column=4, sticky="w")

        # Encoding-Checkboxen
        enc_frame = ttk.LabelFrame(main_frame, text="Kodierung", padding="5")
        enc_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        ttk.Checkbutton(enc_frame, text="ASCII  (1 Byte/Zeichen)",
                        variable=self.scan_ascii).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Checkbutton(enc_frame, text="UTF-16LE  (2 Byte/Zeichen, Hi-Byte=0x00)",
                        variable=self.scan_utf16).grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Checkbutton(enc_frame, text="Nur 4-Byte-Alignment  (Offset % 4 == 0)",
                        variable=self.align4).grid(row=0, column=2, sticky="w")

        # ---- Suche ----
        search_frame = ttk.LabelFrame(main_frame, text="Suche (optional)", padding="5")
        search_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        search_frame.columnconfigure(1, weight=1)

        ttk.Label(search_frame, text="Begriff:").grid(row=0, column=0, sticky="w")
        ttk.Entry(search_frame, textvariable=self.search_string, width=32).grid(
            row=0, column=1, padx=(5, 10), sticky="ew")
        ttk.Checkbutton(search_frame, text="Groß-/Kleinschr.",
                        variable=self.search_case_sensitive).grid(row=0, column=2, padx=(0, 8))
        ttk.Checkbutton(search_frame, text="Regex",
                        variable=self.search_use_regex).grid(row=0, column=3, padx=(0, 8))
        ttk.Checkbutton(search_frame, text="Nur Treffer",
                        variable=self.search_only_matches).grid(row=0, column=4)

        # ---- Buttons ----
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.grid(row=5, column=0, columnspan=3, pady=(0, 6))

        self.scan_button = ttk.Button(ctrl_frame, text="▶ Scan starten", command=self.start_scan)
        self.scan_button.grid(row=0, column=0, padx=(0, 5))
        self.stop_button = ttk.Button(ctrl_frame, text="■ Stop", command=self.stop_scan,
                                      state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(0, 5))
        ttk.Button(ctrl_frame, text="💾 Speichern", command=self.save_results).grid(
            row=0, column=2, padx=(0, 5))
        ttk.Button(ctrl_frame, text="🗑 Löschen", command=self.clear_results).grid(
            row=0, column=3)

        # ---- Progress ----
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.status_label = ttk.Label(main_frame, text="Bereit")
        self.status_label.grid(row=7, column=0, columnspan=3, sticky="w")

        # ---- Ergebnisse ----
        res_frame = ttk.LabelFrame(main_frame, text="Ergebnisse", padding="5")
        res_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        res_frame.columnconfigure(0, weight=1)
        res_frame.rowconfigure(0, weight=1)

        self.results_text = scrolledtext.ScrolledText(
            res_frame, height=20, width=80, font=("Courier New", 9))
        self.results_text.grid(row=0, column=0, sticky="nsew")
        self.results_text.tag_config("highlight", background="#ffff00", foreground="#000000")
        self.results_text.tag_config("header",    foreground="#0055cc")
        self.results_text.tag_config("enc_ascii",  foreground="#006600")
        self.results_text.tag_config("enc_utf16",  foreground="#880000")

        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(8, weight=1)

    def _update_browse_label(self):
        if self.scan_mode.get() == "file":
            self.browse_btn.config(text="Datei wählen")
        else:
            self.browse_btn.config(text="Ordner wählen")

    # --------------------------------------------------------------- Dialoge --

    def select_path(self):
        if self.scan_mode.get() == "file":
            path = filedialog.askopenfilename(
                filetypes=[("Binärdateien", "*.xbe *.lin *.umd *.rsm *.bin *.exe *.dll"),
                           ("Alle Dateien", "*.*")])
        else:
            path = filedialog.askdirectory()
        if path:
            self.selected_path.set(path)

    # --------------------------------------------------------------- Scan --

    def start_scan(self):
        path = self.selected_path.get().strip()
        if not path:
            messagebox.showerror("Fehler", "Bitte Datei oder Ordner auswählen!")
            return
        if not os.path.exists(path):
            messagebox.showerror("Fehler", "Pfad existiert nicht!")
            return
        if not self.scan_ascii.get() and not self.scan_utf16.get():
            messagebox.showerror("Fehler", "Mindestens eine Kodierung auswählen!")
            return

        # Suchpattern validieren
        search_term = self.search_string.get().strip()
        self._search_pattern = None
        if search_term:
            flags = 0 if self.search_case_sensitive.get() else re.IGNORECASE
            try:
                if self.search_use_regex.get():
                    self._search_pattern = re.compile(search_term, flags)
                else:
                    self._search_pattern = re.compile(re.escape(search_term), flags)
            except re.error as e:
                messagebox.showerror("Regex-Fehler", f"Ungültiger Ausdruck:\n{e}")
                return

        self.scanning = True
        self.scan_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.progress.start()
        self.results_text.delete(1.0, tk.END)

        t = threading.Thread(target=self._scan_worker, daemon=True)
        t.start()

    def stop_scan(self):
        self.scanning = False

    def _scan_worker(self):
        path = self.selected_path.get().strip()
        mode = self.scan_mode.get()

        if mode == "file":
            files = [Path(path)]
        else:
            files = list(Path(path).rglob('*'))

        total_files   = 0
        total_strings = 0

        for fp in files:
            if not self.scanning:
                break
            if not fp.is_file():
                continue
            total_files += 1
            self.root.after(0, lambda n=fp.name, i=total_files:
                            self.status_label.config(text=f"[{i}] {n}"))
            try:
                with open(fp, 'rb') as f:
                    data = f.read()
                results = self._extract_strings(data)
                total_strings += len(results)
                if results:
                    self.root.after(0, lambda p=fp, r=results: self._display_results(p, r))
            except (PermissionError, OSError) as e:
                self.root.after(0, lambda p=fp, err=str(e):
                                self.results_text.insert(tk.END, f"\nFEHLER {p}: {err}\n"))

        if self.scanning:
            self.root.after(0, lambda: self._scan_done(total_files, total_strings))
        else:
            self.root.after(0, lambda: self.status_label.config(text="Gestoppt."))
        self.scanning = False
        self.root.after(0, self._reset_buttons)

    def _reset_buttons(self):
        self.scan_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.progress.stop()

    def _scan_done(self, files, strings):
        self.status_label.config(
            text=f"Fertig — {files} Datei(en), {strings} String(s) gefunden.")

    # --------------------------------------------------------------- Extraktion --

    def _extract_strings(self, data: bytes) -> list:
        """
        Gibt Liste von (offset, encoding_label, decoded_string) zurück.
        encoding_label: 'ASCII' oder 'UTF16'
        """
        results = []
        min_len  = self.min_length.get()
        do_align = self.align4.get()

        # --- ASCII ---
        if self.scan_ascii.get():
            i = 0
            n = len(data)
            while i < n:
                # Alignment-Check
                if do_align and (i % 4 != 0):
                    i += 1
                    continue
                # Lese so lange druckbare ASCII-Zeichen
                j = i
                while j < n and 0x20 <= data[j] <= 0x7E:
                    j += 1
                length = j - i
                if length >= min_len:
                    s = data[i:j].decode('ascii', errors='replace')
                    results.append((i, 'ASCII', s))
                    i = j  # direkt hinter dem String weiter
                else:
                    i += 4 if do_align else 1

        # --- UTF-16LE ---
        if self.scan_utf16.get():
            i = 0
            n = len(data)
            while i < n - 1:
                if do_align and (i % 4 != 0):
                    i += 2
                    continue
                j = i
                while j + 1 < n and data[j+1] == 0x00 and 0x20 <= data[j] <= 0x7E:
                    j += 2
                char_count = (j - i) // 2
                if char_count >= min_len:
                    s = data[i:j].decode('utf-16-le', errors='replace')
                    results.append((i, 'UTF16', s))
                    i = j
                else:
                    i += 4 if do_align else 2

        # Nach Offset sortieren
        results.sort(key=lambda x: x[0])
        return results

    # --------------------------------------------------------------- Anzeige --

    def _display_results(self, file_path: Path, results: list):
        sp         = self._search_pattern
        only       = self.search_only_matches.get() and sp is not None
        max_disp   = self.max_strings_per_file.get()

        # Filter
        filtered = []
        for offset, enc, s in results:
            if sp is None or sp.search(s):
                filtered.append((offset, enc, s, True))   # True = Treffer
            elif not only:
                filtered.append((offset, enc, s, False))

        if only and not filtered:
            return

        # Header
        self.results_text.insert(tk.END, f"\n{'─'*70}\n", "header")
        self.results_text.insert(tk.END, f"  {file_path}\n", "header")
        count_ascii = sum(1 for _, e, _, _ in filtered if e == 'ASCII')
        count_utf16 = sum(1 for _, e, _, _ in filtered if e == 'UTF16')
        info = f"  {len(filtered)} String(s)"
        if self.scan_ascii.get() and self.scan_utf16.get():
            info += f"  [ASCII: {count_ascii}  UTF-16LE: {count_utf16}]"
        if self.align4.get():
            info += "  [4-Byte-Align]"
        if sp:
            hits = sum(1 for *_, m in filtered if m)
            info += f"  [Suchtreffer: {hits}]"
        self.results_text.insert(tk.END, info + "\n", "header")
        self.results_text.insert(tk.END, f"{'─'*70}\n", "header")

        display = filtered if max_disp == 0 else filtered[:max_disp]

        for offset, enc, s, is_hit in display:
            clean = s.replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t')
            enc_tag = "enc_ascii" if enc == 'ASCII' else "enc_utf16"
            prefix = f"  0x{offset:08X}  [{enc:5s}]  "
            line_start = self.results_text.index(tk.END)
            self.results_text.insert(tk.END, prefix, enc_tag)
            self.results_text.insert(tk.END, clean + "\n")

            # Suchtreffer im String markieren
            if sp and is_hit:
                for m in sp.finditer(clean):
                    col_start = len(prefix) + m.start()
                    col_end   = len(prefix) + m.end()
                    self.results_text.tag_add(
                        "highlight",
                        f"{line_start}+{col_start}c",
                        f"{line_start}+{col_end}c")

        if max_disp > 0 and len(filtered) > max_disp:
            self.results_text.insert(
                tk.END,
                f"  ... {len(filtered) - max_disp} weitere (Limit erreicht)\n")

        self.results_text.see(tk.END)

    # --------------------------------------------------------------- I/O --

    def clear_results(self):
        self.results_text.delete(1.0, tk.END)

    def save_results(self):
        content = self.results_text.get(1.0, tk.END).strip()
        if not content:
            messagebox.showwarning("Warnung", "Keine Ergebnisse zum Speichern!")
            return
        fp = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Textdateien", "*.txt"), ("Alle", "*.*")])
        if fp:
            try:
                with open(fp, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo("Gespeichert", fp)
            except Exception as e:
                messagebox.showerror("Fehler", str(e))


def main():
    root = tk.Tk()
    app = BinaryStringScanner(root)
    root.mainloop()


if __name__ == "__main__":
    main()
