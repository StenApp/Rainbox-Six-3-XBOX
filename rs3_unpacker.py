import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import zlib
import os
import struct


def decompress_chunks(data):
    """Extract all zlib chunks from data. Returns list of decompressed blobs."""
    chunks = []
    offset = 0
    while offset + 8 <= len(data):
        try:
            uncompressed_size = struct.unpack_from('<I', data, offset)[0]
            compressed_size   = struct.unpack_from('<I', data, offset + 4)[0]
        except struct.error:
            break

        if compressed_size == 0 or uncompressed_size == 0:
            break
        if offset + 8 + compressed_size > len(data):
            break

        chunk_data = data[offset + 8 : offset + 8 + compressed_size]
        try:
            decompressed = zlib.decompress(chunk_data)
        except zlib.error as e:
            break

        chunks.append(decompressed)
        offset += 8 + compressed_size

    return chunks, offset


def process_file(src_path, out_dir, log):
    base = os.path.splitext(os.path.basename(src_path))[0]
    ext  = os.path.splitext(src_path)[1]  # keep original extension

    with open(src_path, 'rb') as f:
        data = f.read()

    chunks, consumed = decompress_chunks(data)

    if not chunks:
        log(f"  SKIP  {os.path.basename(src_path)} — keine zlib-Chunks gefunden")
        return 0

    # always concatenate all chunks into one file
    out_path = os.path.join(out_dir, base + ext)
    total_size = sum(len(c) for c in chunks)
    with open(out_path, 'wb') as f:
        for chunk in chunks:
            f.write(chunk)
    if len(chunks) == 1:
        log(f"  OK    {os.path.basename(src_path)} → {os.path.basename(out_path)} ({total_size} bytes)")
    else:
        log(f"  OK    {os.path.basename(src_path)} → {os.path.basename(out_path)} "
            f"({len(chunks)} chunks, {total_size:,} bytes)")

    return 1


# ── GUI ──────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RS3 Unpacker")
        self.resizable(True, True)
        self.minsize(560, 400)
        self._build()

    def _build(self):
        pad = dict(padx=8, pady=4)

        # Mode
        mode_frame = ttk.LabelFrame(self, text="Modus")
        mode_frame.pack(fill='x', **pad)

        self.mode = tk.StringVar(value="file")
        ttk.Radiobutton(mode_frame, text="Einzelne Datei", variable=self.mode,
                        value="file",   command=self._on_mode).pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(mode_frame, text="Ordner (Batch)", variable=self.mode,
                        value="folder", command=self._on_mode).pack(side='left', padx=8, pady=4)

        # Input
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

        # Filter (batch only)
        self.filter_frame = ttk.Frame(input_frame)
        self.filter_frame.grid(row=1, column=0, columnspan=3, sticky='w', padx=6, pady=2)
        ttk.Label(self.filter_frame, text="Dateifilter:").pack(side='left')
        self.filter_var = tk.StringVar(value="*")
        ttk.Entry(self.filter_frame, textvariable=self.filter_var, width=16).pack(
            side='left', padx=4)
        ttk.Label(self.filter_frame, text="(z.B. *.lin  oder  *)").pack(side='left')
        self.filter_frame.grid_remove()

        # Output
        out_frame = ttk.LabelFrame(self, text="Ausgabeordner")
        out_frame.pack(fill='x', **pad)
        out_frame.columnconfigure(1, weight=1)

        self.out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.out_var).grid(
            row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(out_frame, text="…", width=3,
                   command=self._browse_output).grid(row=0, column=2, padx=4, pady=4)

        # Run button
        ttk.Button(self, text="Extrahieren", command=self._run).pack(pady=6)

        # Log
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill='both', expand=True, **pad)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, state='disabled', wrap='none',
                                font=('Consolas', 9))
        self.log_text.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.log_text['yscrollcommand'] = sb.set

    def _on_mode(self):
        if self.mode.get() == "folder":
            self.input_label.config(text="Ordner:")
            self.filter_frame.grid()
        else:
            self.input_label.config(text="Datei:")
            self.filter_frame.grid_remove()
        self.input_var.set("")

    def _browse_input(self):
        if self.mode.get() == "file":
            p = filedialog.askopenfilename(title="Datei wählen")
        else:
            p = filedialog.askdirectory(title="Ordner wählen")
        if p:
            self.input_var.set(p)
            base = os.path.dirname(p) if self.mode.get() == "file" else p
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

        src_dir = os.path.dirname(src) if self.mode.get() == "file" else src
        if os.path.normpath(out_dir) == os.path.normpath(src_dir):
            if not messagebox.askyesno("Warnung", "Ausgabeordner ist gleich dem Quellordner!\nOriginal-Dateien könnten überschrieben werden.\n\nTrotzdem fortfahren?"):
                return
        os.makedirs(out_dir, exist_ok=True)
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

        total_files = 0
        total_chunks = 0

        if self.mode.get() == "file":
            if not os.path.isfile(src):
                messagebox.showerror("Fehler", "Datei nicht gefunden.")
                return
            self._log(f"Verarbeite: {src}")
            n = process_file(src, out_dir, self._log)
            total_files  = 1
            total_chunks = n
        else:
            if not os.path.isdir(src):
                messagebox.showerror("Fehler", "Ordner nicht gefunden.")
                return
            filt = self.filter_var.get().strip() or "*"
            import fnmatch
            files = [f for f in os.listdir(src)
                     if fnmatch.fnmatch(f.lower(), filt.lower())
                     and os.path.isfile(os.path.join(src, f))]
            if not files:
                self._log("Keine passenden Dateien gefunden.")
                return
            self._log(f"{len(files)} Datei(en) gefunden — Filter: {filt}\n")
            for fname in sorted(files):
                n = process_file(os.path.join(src, fname), out_dir, self._log)
                total_files  += 1
                total_chunks += n

        self._log(f"\nFertig — {total_files} Datei(en), {total_chunks} Chunk(s) extrahiert.")
        self._log(f"Ausgabe: {out_dir}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
