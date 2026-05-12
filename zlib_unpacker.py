"""
Universal zlib Unpacker
-----------------------
Scans files for zlib magic bytes (78 9C / 78 DA / 78 01 / 78 5E)
and attempts decompression from every found offset.

Modes:
  - Single file
  - Folder batch (with file filter)

Output:
  - One file per successful decompressed stream, original extension kept
  - Multiple hits in one file → _hit000, _hit001 ...
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import zlib
import os
import struct
import fnmatch


# zlib magic bytes (CMF byte, then FLG byte combinations)
# 78 01 = low compression
# 78 9C = default compression
# 78 DA = best compression
# 78 5E = fast compression
ZLIB_MAGIC = [b'\x78\x01', b'\x78\x9C', b'\x78\xDA', b'\x78\x5E']

# Minimum decompressed size to count as valid hit (filter garbage)
MIN_DECOMPRESSED = 64


def find_zlib_streams(data, log=None):
    """
    Scan data for all zlib magic offsets and attempt decompression.
    Returns list of (offset, decompressed_bytes).
    """
    hits = []
    seen_offsets = set()

    for magic in ZLIB_MAGIC:
        start = 0
        while True:
            pos = data.find(magic, start)
            if pos == -1:
                break
            if pos not in seen_offsets:
                seen_offsets.add(pos)
                try:
                    decompressed = zlib.decompress(data[pos:])
                    if len(decompressed) >= MIN_DECOMPRESSED:
                        hits.append((pos, decompressed))
                        if log:
                            log(f"    zlib hit @ 0x{pos:08X} ({magic.hex().upper()}) → "
                                f"{len(decompressed):,} bytes decompressed")
                except zlib.error:
                    pass
            start = pos + 1

    # sort by offset
    hits.sort(key=lambda x: x[0])
    return hits


def rs3_chunks(data, log=None):
    """
    Try RS3-style chunked format:
    [DWORD uncompressed_size][DWORD compressed_size][zlib data] repeated
    Returns list of decompressed blobs, or empty list if not applicable.
    """
    chunks = []
    offset = 0
    while offset + 8 <= len(data):
        uncompressed_size = struct.unpack_from('<I', data, offset)[0]
        compressed_size   = struct.unpack_from('<I', data, offset + 4)[0]

        if compressed_size == 0 or uncompressed_size == 0:
            break
        if offset + 8 + compressed_size > len(data):
            break
        # sanity: ratio shouldn't be insane
        if uncompressed_size > compressed_size * 1000:
            break

        chunk_data = data[offset + 8: offset + 8 + compressed_size]

        # must start with zlib magic
        if chunk_data[:2] not in ZLIB_MAGIC:
            break

        try:
            decompressed = zlib.decompress(chunk_data)
        except zlib.error:
            break

        if len(decompressed) != uncompressed_size:
            # size mismatch — probably not RS3 format, fall through
            break

        chunks.append(decompressed)
        offset += 8 + compressed_size

        if log:
            log(f"    RS3 chunk @ 0x{offset - 8 - compressed_size:08X} → "
                f"{len(decompressed):,} bytes")

    return chunks


def process_file(src_path, out_dir, log, mode="auto"):
    """
    mode: 'auto' | 'rs3' | 'scan'
    """
    base = os.path.splitext(os.path.basename(src_path))[0]
    ext  = os.path.splitext(src_path)[1]

    with open(src_path, 'rb') as f:
        data = f.read()

    results = []  # list of decompressed blobs

    # --- RS3 chunked mode ---
    if mode in ('auto', 'rs3'):
        chunks = rs3_chunks(data, log)
        if chunks:
            log(f"  RS3   {os.path.basename(src_path)} — {len(chunks)} chunk(s)")
            results = chunks

    # --- Raw scan mode ---
    if not results and mode in ('auto', 'scan'):
        hits = find_zlib_streams(data, log)
        if hits:
            log(f"  SCAN  {os.path.basename(src_path)} — {len(hits)} hit(s)")
            results = [blob for _, blob in hits]

    if not results:
        log(f"  SKIP  {os.path.basename(src_path)} — nichts gefunden")
        return 0

    out_path = os.path.join(out_dir, base + ext)
    total_size = sum(len(r) for r in results)
    with open(out_path, 'wb') as f:
        for blob in results:
            f.write(blob)
    if len(results) == 1:
        log(f"    → {os.path.basename(out_path)} ({total_size:,} bytes)")
    else:
        log(f"    → {os.path.basename(out_path)} ({len(results)} streams concatenated, {total_size:,} bytes)")

    return len(results)


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Universal zlib Unpacker")
        self.resizable(True, True)
        self.minsize(600, 480)
        self._build()

    def _build(self):
        pad = dict(padx=8, pady=4)

        # ── Input mode ──
        mode_frame = ttk.LabelFrame(self, text="Eingabemodus")
        mode_frame.pack(fill='x', **pad)

        self.input_mode = tk.StringVar(value="file")
        ttk.Radiobutton(mode_frame, text="Einzelne Datei", variable=self.input_mode,
                        value="file",   command=self._on_input_mode).pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(mode_frame, text="Ordner (Batch)", variable=self.input_mode,
                        value="folder", command=self._on_input_mode).pack(side='left', padx=8, pady=4)

        # ── Input path ──
        input_frame = ttk.LabelFrame(self, text="Eingabe")
        input_frame.pack(fill='x', **pad)
        input_frame.columnconfigure(1, weight=1)

        self.input_label = ttk.Label(input_frame, text="Datei:")
        self.input_label.grid(row=0, column=0, sticky='w', padx=6, pady=4)
        self.input_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.input_var).grid(
            row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(input_frame, text="…", width=3,
                   command=self._browse_input).grid(row=0, column=2, padx=4, pady=4)

        self.filter_frame = ttk.Frame(input_frame)
        self.filter_frame.grid(row=1, column=0, columnspan=3, sticky='w', padx=6, pady=2)
        ttk.Label(self.filter_frame, text="Dateifilter:").pack(side='left')
        self.filter_var = tk.StringVar(value="*")
        ttk.Entry(self.filter_frame, textvariable=self.filter_var, width=16).pack(side='left', padx=4)
        ttk.Label(self.filter_frame, text="z.B.  *.lin  *.utx  *").pack(side='left')
        self.filter_frame.grid_remove()

        # ── Output ──
        out_frame = ttk.LabelFrame(self, text="Ausgabeordner")
        out_frame.pack(fill='x', **pad)
        out_frame.columnconfigure(1, weight=1)

        self.out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.out_var).grid(
            row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(out_frame, text="…", width=3,
                   command=self._browse_output).grid(row=0, column=2, padx=4, pady=4)

        # ── Extraction mode ──
        xmode_frame = ttk.LabelFrame(self, text="Extraktionsmodus")
        xmode_frame.pack(fill='x', **pad)

        self.xmode = tk.StringVar(value="auto")
        ttk.Radiobutton(xmode_frame, text="Auto (RS3-Chunks zuerst, dann Scan)",
                        variable=self.xmode, value="auto").pack(anchor='w', padx=8, pady=2)
        ttk.Radiobutton(xmode_frame, text="RS3-Chunks only  [Header: size+size+zlib]",
                        variable=self.xmode, value="rs3").pack(anchor='w', padx=8, pady=2)
        ttk.Radiobutton(xmode_frame, text="Raw Scan  [sucht 78 9C / 78 DA / 78 01 / 78 5E]",
                        variable=self.xmode, value="scan").pack(anchor='w', padx=8, pady=2)

        # ── Run ──
        ttk.Button(self, text="Extrahieren", command=self._run).pack(pady=6)

        # ── Log ──
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill='both', expand=True, **pad)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, state='disabled', wrap='none',
                                font=('Consolas', 9))
        self.log_text.grid(row=0, column=0, sticky='nsew')
        sb_y = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb_y.grid(row=0, column=1, sticky='ns')
        sb_x = ttk.Scrollbar(log_frame, orient='horizontal', command=self.log_text.xview)
        sb_x.grid(row=1, column=0, sticky='ew')
        self.log_text['yscrollcommand'] = sb_y.set
        self.log_text['xscrollcommand'] = sb_x.set

    def _on_input_mode(self):
        if self.input_mode.get() == "folder":
            self.input_label.config(text="Ordner:")
            self.filter_frame.grid()
        else:
            self.input_label.config(text="Datei:")
            self.filter_frame.grid_remove()
        self.input_var.set("")

    def _browse_input(self):
        if self.input_mode.get() == "file":
            p = filedialog.askopenfilename(title="Datei wählen")
        else:
            p = filedialog.askdirectory(title="Ordner wählen")
        if p:
            self.input_var.set(p)
            base = os.path.dirname(p) if self.input_mode.get() == "file" else p
            self.out_var.set(os.path.join(base, "_unpacked"))

    def _browse_output(self):
        p = filedialog.askdirectory(title="Ausgabeordner wählen")
        if p:
            self.out_var.set(p)

    def _log(self, msg):
        self.log_text.config(state='normal')
        self.log_text.insert('end', msg + "\n")
        self.log_text.see('end')
        self.log_text.config(state='disabled')
        self.update_idletasks()

    def _run(self):
        src     = self.input_var.get().strip()
        out_dir = self.out_var.get().strip()

        if not src or not out_dir:
            messagebox.showwarning("Fehler", "Eingabe und Ausgabeordner angeben.")
            return

        src_dir = os.path.dirname(src) if self.input_mode.get() == "file" else src
        if os.path.normpath(out_dir) == os.path.normpath(src_dir):
            if not messagebox.askyesno("Warnung", "Ausgabeordner ist gleich dem Quellordner!\nOriginal-Dateien könnten überschrieben werden.\n\nTrotzdem fortfahren?"):
                return
        os.makedirs(out_dir, exist_ok=True)
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

        xmode = self.xmode.get()
        total_files = 0
        total_hits  = 0

        if self.input_mode.get() == "file":
            if not os.path.isfile(src):
                messagebox.showerror("Fehler", "Datei nicht gefunden.")
                return
            self._log(f"Verarbeite: {src}\n")
            n = process_file(src, out_dir, self._log, xmode)
            total_files = 1
            total_hits  = n
        else:
            if not os.path.isdir(src):
                messagebox.showerror("Fehler", "Ordner nicht gefunden.")
                return
            filt  = self.filter_var.get().strip() or "*"
            files = [f for f in os.listdir(src)
                     if fnmatch.fnmatch(f.lower(), filt.lower())
                     and os.path.isfile(os.path.join(src, f))]
            if not files:
                self._log("Keine passenden Dateien gefunden.")
                return
            self._log(f"{len(files)} Datei(en) — Filter: {filt}   Modus: {xmode}\n")
            for fname in sorted(files):
                n = process_file(os.path.join(src, fname), out_dir, self._log, xmode)
                total_files += 1
                total_hits  += n
                self._log("")

        self._log(f"\n{'─'*50}")
        self._log(f"Fertig — {total_files} Datei(en), {total_hits} Stream(s) extrahiert.")
        self._log(f"Ausgabe: {out_dir}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
