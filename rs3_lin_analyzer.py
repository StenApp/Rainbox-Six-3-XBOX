"""
RS3 Xbox LIN Analyzer
----------------------
Analysiert entpackte LIN-Dateien (nach RS3-Chunk-Dekompression):
- Findet alle UE2-Package-Blöcke
- Prüft ob Blöcke eigenständig sind (Offsets innerhalb des Blocks)
- Liest Name-Tables, Import-Tables
- Zeigt Abhängigkeiten zwischen Blöcken
- Exportiert einzelne Blöcke zur manuellen Prüfung in UE Explorer
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import struct, zlib, os, re
from collections import defaultdict


UE_MAGIC     = 0x9E2A83C1
UE_MAGIC_B   = bytes([0xC1, 0x83, 0x2A, 0x9E])


# ── Dekompression ─────────────────────────────────────────────────────────────

def decompress_lin(data):
    """RS3 Chunk-Format: [uint32 uncomp][uint32 comp][zlib] * N"""
    chunks = []
    offset = 0
    while offset + 8 <= len(data):
        uncomp = struct.unpack_from('<I', data, offset)[0]
        comp   = struct.unpack_from('<I', data, offset + 4)[0]
        if comp == 0 or uncomp == 0:
            break
        if offset + 8 + comp > len(data):
            break
        chunk_data = data[offset + 8: offset + 8 + comp]
        if chunk_data[:2] not in (b'\x78\x9c', b'\x78\xda', b'\x78\x01', b'\x78\x5e'):
            break
        try:
            dec = zlib.decompress(chunk_data)
            if len(dec) != uncomp:
                break
            chunks.append(dec)
            offset += 8 + comp
        except zlib.error:
            break
    return b''.join(chunks), len(chunks)


# ── UE2 Package Parsing ───────────────────────────────────────────────────────

def read_compact(data, pos):
    b = data[pos]; pos += 1
    val = b & 0x3F
    neg = bool(b & 0x80)
    if b & 0x40:
        b = data[pos]; pos += 1
        val |= (b & 0x7F) << 6
        if b & 0x80:
            b = data[pos]; pos += 1
            val |= (b & 0x7F) << 13
            if b & 0x80:
                b = data[pos]; pos += 1
                val |= (b & 0x7F) << 20
                if b & 0x80:
                    b = data[pos]; pos += 1
                    val |= (b & 0x1F) << 27
    if neg:
        val = -val
    return val, pos


def parse_package(data, base=0):
    """Parst UE2-Package-Header und Name/Import-Tabellen."""
    result = {}

    try:
        magic  = struct.unpack_from('<I', data, base + 0)[0]
        if magic != UE_MAGIC:
            return None

        ver    = struct.unpack_from('<H', data, base + 4)[0]
        lic    = struct.unpack_from('<H', data, base + 6)[0]
        flags  = struct.unpack_from('<I', data, base + 8)[0]
        nc     = struct.unpack_from('<I', data, base + 0x0C)[0]
        noff   = struct.unpack_from('<I', data, base + 0x10)[0]
        ec     = struct.unpack_from('<I', data, base + 0x14)[0]
        eoff   = struct.unpack_from('<I', data, base + 0x18)[0]
        ic     = struct.unpack_from('<I', data, base + 0x1C)[0]
        ioff   = struct.unpack_from('<I', data, base + 0x20)[0]
        guid   = data[base + 0x24: base + 0x34]

        result.update(dict(
            ver=ver, lic=lic, flags=flags,
            nc=nc, noff=noff, ec=ec, eoff=eoff,
            ic=ic, ioff=ioff, guid=guid.hex(),
            base=base
        ))

        # Namen lesen
        names = []
        pos = base + noff
        for _ in range(min(nc, 20000)):
            if pos >= len(data): break
            slen = data[pos]; pos += 1
            raw_nm = data[pos: pos + slen - 1]; nm = raw_nm.decode("ascii", "replace").replace("Fffd", ".")
            pos += slen + 4
            names.append(nm)
        result['names'] = names
        result['name_table_end'] = pos - base  # relativ zum base

        # Offsets-Sanity: liegen eoff/ioff innerhalb des Blocks?
        # Wir wissen die Blockgröße erst später, aber wir können prüfen
        # ob sie >= noff sind (sinnvolle Reihenfolge)
        result['offsets_sane'] = (
            noff > 0x20 and
            noff < 0x10000 and
            nc < 50000 and
            ec < 50000 and
            ic < 50000
        )

        # Import-Tabelle lesen
        imports = []
        pos = base + ioff
        try:
            for _ in range(min(ic, 5000)):
                if pos + 8 >= len(data): break
                pkg_idx, pos = read_compact(data, pos)
                cls_idx, pos = read_compact(data, pos)
                outer = struct.unpack_from('<i', data, pos)[0]; pos += 4
                name_idx, pos = read_compact(data, pos)
                pkg = names[pkg_idx]  if 0 <= pkg_idx  < len(names) else f'?{pkg_idx}'
                cls = names[cls_idx]  if 0 <= cls_idx  < len(names) else f'?{cls_idx}'
                nm  = names[name_idx] if 0 <= name_idx < len(names) else f'?{name_idx}'
                imports.append((pkg, cls, nm))
        except Exception:
            pass
        result['imports'] = imports

        # Externe Packages aus Imports
        ext_pkgs = sorted(set(
            pkg for pkg, cls, nm in imports
            if pkg not in ('Core', 'Engine', '') and len(pkg) > 1
            and not pkg.startswith('?')
        ))
        result['ext_packages'] = ext_pkgs

    except Exception as e:
        result['parse_error'] = str(e)

    return result


def find_blocks(data):
    """Alle UE2-Magic-Offsets finden."""
    hits = []
    pos = 0
    while True:
        idx = data.find(UE_MAGIC_B, pos)
        if idx == -1:
            break
        if struct.unpack_from('<I', data, idx)[0] == UE_MAGIC:
            hits.append(idx)
        pos = idx + 1
    return hits


def analyze_block_independence(data, base, end):
    """
    Prüft ob ein Block eigenständig ist:
    - eoff und ioff müssen innerhalb [base, end] liegen
    - Name-Table muss lesbar sein
    - Erste Namen müssen ASCII-Text sein
    """
    block_size = end - base
    pkg = parse_package(data, base)
    if not pkg:
        return False, "Kein gültiger Header"

    eoff = pkg['eoff']
    ioff = pkg['ioff']
    ec   = pkg['ec']
    ic   = pkg['ic']

    issues = []

    # Offsets innerhalb des Blocks?
    if eoff >= block_size:
        issues.append(f"eoff=0x{eoff:X} > Blockgröße=0x{block_size:X} (Stub/extern)")
    if ioff >= block_size:
        issues.append(f"ioff=0x{ioff:X} > Blockgröße=0x{block_size:X} (Stub/extern)")

    # Können wir die Export-Tabelle lesen?
    if eoff < block_size and ec > 0:
        try:
            # Ersten Export-Entry lesen
            pos = base + eoff
            ci, pos2 = read_compact(data, pos)
            si, pos2 = read_compact(data, pos2)
            pi = struct.unpack_from('<i', data, pos2)[0]
            issues_exp = []
        except Exception as e:
            issues.append(f"Export-Tabelle nicht lesbar: {e}")

    independent = len(issues) == 0
    return independent, issues


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RS3 LIN Analyzer")
        self.resizable(True, True)
        self.minsize(900, 600)
        self._full_data = None
        self._blocks    = []
        self._pkgs      = []
        self._build()

    def _build(self):
        pad = dict(padx=6, pady=3)

        # Top: Eingabe
        top = ttk.Frame(self)
        top.pack(fill='x', **pad)

        ttk.Label(top, text="LIN-Datei:").pack(side='left')
        self.in_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.in_var, width=60).pack(
            side='left', padx=4)
        ttk.Button(top, text="…", width=3,
                   command=self._browse).pack(side='left')
        ttk.Button(top, text="Analysieren",
                   command=self._analyze).pack(side='left', padx=8)

        # Mitte: Paned
        paned = ttk.PanedWindow(self, orient='horizontal')
        paned.pack(fill='both', expand=True, **pad)

        # Links: Block-Liste
        left = ttk.LabelFrame(paned, text="Blöcke")
        paned.add(left, weight=1)

        cols = ('idx', 'offset', 'size', 'lic', 'nc', 'ec', 'ic', 'standalone', 'name')
        self.tree = ttk.Treeview(left, columns=cols, show='headings',
                                 selectmode='browse')
        for col, w, anchor in [
            ('idx',        35,  'center'),
            ('offset',     90,  'center'),
            ('size',       90,  'e'),
            ('lic',        40,  'center'),
            ('nc',         55,  'e'),
            ('ec',         55,  'e'),
            ('ic',         45,  'e'),
            ('standalone', 80,  'center'),
            ('name',      160,  'w'),
        ]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor=anchor, stretch=(col=='name'))

        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree['yscrollcommand'] = sb.set
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # Rechts: Detail + Export
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        # Detail-Text
        detail_frame = ttk.LabelFrame(right, text="Block-Detail")
        detail_frame.pack(fill='both', expand=True, **pad)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)

        self.detail = tk.Text(detail_frame, wrap='none',
                              font=('Consolas', 9), state='disabled')
        self.detail.grid(row=0, column=0, sticky='nsew')
        sb2 = ttk.Scrollbar(detail_frame, command=self.detail.yview)
        sb2.grid(row=0, column=1, sticky='ns')
        sb3 = ttk.Scrollbar(detail_frame, orient='horizontal',
                             command=self.detail.xview)
        sb3.grid(row=1, column=0, sticky='ew')
        self.detail['yscrollcommand'] = sb2.set
        self.detail['xscrollcommand'] = sb3.set

        # Export-Buttons
        btn_frame = ttk.Frame(right)
        btn_frame.pack(fill='x', padx=6, pady=2)
        ttk.Button(btn_frame, text="Selektierten Block exportieren",
                   command=self._export_selected).pack(side='left', padx=4)
        ttk.Button(btn_frame, text="Alle Blöcke exportieren",
                   command=self._export_all).pack(side='left', padx=4)
        self.patch_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btn_frame, text="Licensee patchen (Xbox→PC)",
                        variable=self.patch_var).pack(side='left', padx=8)

        # Log unten
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill='x', **pad)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=5, wrap='none',
                           font=('Consolas', 9), state='disabled')
        self.log.grid(row=0, column=0, sticky='ew')
        sb4 = ttk.Scrollbar(log_frame, command=self.log.yview)
        sb4.grid(row=0, column=1, sticky='ns')
        self.log['yscrollcommand'] = sb4.set

    def _browse(self):
        p = filedialog.askopenfilename(
            title="LIN-Datei wählen",
            filetypes=[("LIN", "*.lin"), ("Alle", "*.*")])
        if p:
            self.in_var.set(p)

    def _log(self, msg):
        self.log.config(state='normal')
        self.log.insert('end', msg + "\n")
        self.log.see('end')
        self.log.config(state='disabled')
        self.update_idletasks()

    def _detail(self, msg):
        self.detail.config(state='normal')
        self.detail.insert('end', msg)
        self.detail.config(state='disabled')

    def _detail_clear(self):
        self.detail.config(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.config(state='disabled')

    def _analyze(self):
        path = self.in_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror("Fehler", "Datei nicht gefunden.")
            return

        self._log(f"Lese: {os.path.basename(path)}")
        with open(path, 'rb') as f:
            raw = f.read()
        self._log(f"  Größe: {len(raw):,} bytes")

        # Entpacken
        full, n_chunks = decompress_lin(raw)
        if not full:
            self._log("  FEHLER: Nicht entpackbar — versuche direkt als entpackte Daten")
            full = raw
            n_chunks = 0
        else:
            self._log(f"  Entpackt: {n_chunks} Chunks -> {len(full):,} bytes")

        self._full_data = full

        # Blöcke finden
        hits = find_blocks(full)
        self._log(f"  UE2-Magic Treffer: {len(hits)}")

        # Blöcke analysieren
        self._blocks = []
        self._pkgs   = []

        for i, h in enumerate(hits):
            end = hits[i + 1] if i + 1 < len(hits) else len(full)
            block_size = end - h
            pkg = parse_package(full, h)
            if not pkg:
                continue

            independent, issues = analyze_block_independence(full, h, end)

            # Name erraten
            guessed_name = self._guess_name(pkg, i)

            self._blocks.append({
                'idx':         i,
                'offset':      h,
                'end':         end,
                'size':        block_size,
                'pkg':         pkg,
                'independent': independent,
                'issues':      issues,
                'name':        guessed_name,
            })
            self._pkgs.append(pkg)

        # Tree befüllen
        for item in self.tree.get_children():
            self.tree.delete(item)

        for b in self._blocks:
            p = b['pkg']
            standalone = "✓ JA" if b['independent'] else "✗ STUB"
            tag = 'ok' if b['independent'] else 'stub'
            self.tree.insert('', 'end', iid=str(b['idx']),
                values=(
                    b['idx'],
                    f"0x{b['offset']:08X}",
                    f"{b['size']:,}",
                    p.get('lic', '?'),
                    p.get('nc', '?'),
                    p.get('ec', '?'),
                    p.get('ic', '?'),
                    standalone,
                    b['name'],
                ), tags=(tag,))

        self.tree.tag_configure('ok',   foreground='#006600')
        self.tree.tag_configure('stub', foreground='#990000')

        ok_count   = sum(1 for b in self._blocks if b['independent'])
        stub_count = sum(1 for b in self._blocks if not b['independent'])
        self._log(f"  Eigenständig: {ok_count}   Stubs: {stub_count}")
        self._log("  Fertig.\n")

    def _guess_name(self, pkg, idx):
        """Package-Name aus Name-Table erraten."""
        names = pkg.get('names', [])
        name_set = set(names)

        # Heuristiken
        if 'LevelInfo' in name_set or 'MyLevel' in name_set or 'ReachSpec' in name_set:
            return '→ Level (RSM)'
        if 'KMeshProps' in name_set or 'KMeshProps0' in name_set:
            return '→ StaticMesh (USX)'
        if any(n.startswith('AnimNotify') for n in names[:20]):
            return '→ Animation (UKX)'
        if 'R6 R Hand' in name_set or 'FootStepLeft' in name_set:
            return '→ Animation (UKX)'
        if 'USize' in name_set and 'VSize' in name_set and 'Format' in name_set:
            return '→ Textures (UTX)'
        if 'Palette' in name_set and 'MipZero' in name_set:
            return '→ Textures (UTX)'

        # Bekannte Package-Namen in der Name-Table
        for nm in names:
            if nm in ('Al_prison_T', 'Al_prison_TSM', 'Al_Prison_SM',
                      'Airport_T', 'Airport_TSM', 'Alcatraz_T'):
                return f'→ {nm}'

        # Ext-Packages aus Imports
        ext = pkg.get('ext_packages', [])
        if ext:
            return f'refs: {", ".join(ext[:3])}'

        return f'Block_{idx:02d}'

    def _on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        b = next((x for x in self._blocks if x['idx'] == idx), None)
        if not b:
            return

        self._detail_clear()
        p = b['pkg']

        self._detail(f"Block [{b['idx']}]  @ 0x{b['offset']:08X}\n")
        self._detail(f"{'─'*50}\n")
        self._detail(f"Größe:      {b['size']:,} bytes\n")
        self._detail(f"Version:    {p.get('ver','?')}\n")
        self._detail(f"Licensee:   {p.get('lic','?')}\n")
        self._detail(f"Flags:      0x{p.get('flags',0):08X}\n")
        self._detail(f"GUID:       {p.get('guid','?')}\n")
        self._detail(f"Names:      {p.get('nc','?')}  @ +0x{p.get('noff',0):X}\n")
        self._detail(f"Exports:    {p.get('ec','?')}  @ +0x{p.get('eoff',0):X}\n")
        self._detail(f"Imports:    {p.get('ic','?')}  @ +0x{p.get('ioff',0):X}\n")
        self._detail(f"\n")

        # Eigenständigkeit
        if b['independent']:
            self._detail(f"✓ EIGENSTÄNDIG — Export/Import-Offsets liegen im Block\n")
        else:
            self._detail(f"✗ STUB — Probleme:\n")
            for iss in b['issues']:
                self._detail(f"   • {iss}\n")
        self._detail(f"\n")

        # Name-Table (erste 30)
        names = p.get('names', [])
        self._detail(f"Name-Table (erste 30 von {len(names)}):\n")
        for i, nm in enumerate(names[:30]):
            self._detail(f"  [{i:4d}] {nm}\n")
        if len(names) > 30:
            self._detail(f"  ... +{len(names)-30} weitere\n")
        self._detail(f"\n")

        # Externe Package-Referenzen
        ext = p.get('ext_packages', [])
        if ext:
            self._detail(f"Externe Package-Referenzen ({len(ext)}):\n")
            for e in ext:
                self._detail(f"  {e}\n")
        else:
            self._detail(f"Keine externen Package-Referenzen\n")
        self._detail(f"\n")

        # Import-Tabelle (erste 20)
        imports = p.get('imports', [])
        if imports:
            self._detail(f"Import-Tabelle (erste 20 von {len(imports)}):\n")
            for pkg_n, cls_n, obj_n in imports[:20]:
                self._detail(f"  {pkg_n:<20} {cls_n:<15} {obj_n}\n")
            if len(imports) > 20:
                self._detail(f"  ... +{len(imports)-20} weitere\n")

    def _export_block(self, b, out_dir):
        """Einzelnen Block exportieren."""
        if self._full_data is None:
            return

        data = bytearray(self._full_data[b['offset']: b['end']])
        name = b['name'].replace('→ ', '').replace(' ', '_').strip('_')
        if not name or name.startswith('Block'):
            name = f"Block_{b['idx']:02d}"
        # Extension
        if 'RSM' in b['name'] or 'Level' in b['name']:
            ext = '.rsm'
        elif 'USX' in b['name'] or 'StaticMesh' in b['name']:
            ext = '.usx'
        elif 'UKX' in b['name'] or 'Animation' in b['name']:
            ext = '.ukx'
        elif 'UTX' in b['name'] or 'Texture' in b['name']:
            ext = '.utx'
        else:
            ext = '.upk'

        # Licensee patchen
        if self.patch_var.get():
            lic = struct.unpack_from('<H', data, 6)[0]
            patch_map = {21: 13, 20: 12}
            new_lic = patch_map.get(lic)
            if new_lic:
                struct.pack_into('<H', data, 6, new_lic)

        out_path = os.path.join(out_dir, name + ext)
        stem, sfx = os.path.splitext(out_path)
        c = 1
        while os.path.exists(out_path):
            out_path = f"{stem}_{c}{sfx}"; c += 1

        with open(out_path, 'wb') as f:
            f.write(data)

        standalone = "eigenständig" if b['independent'] else "STUB"
        self._log(f"  Exportiert: {os.path.basename(out_path)}  "
                  f"({len(data):,} bytes, {standalone})")

    def _export_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Hinweis", "Keinen Block ausgewählt.")
            return
        idx = int(sel[0])
        b = next((x for x in self._blocks if x['idx'] == idx), None)
        if not b:
            return
        out_dir = filedialog.askdirectory(title="Ausgabeordner")
        if not out_dir:
            return
        os.makedirs(out_dir, exist_ok=True)
        self._export_block(b, out_dir)

    def _export_all(self):
        if not self._blocks:
            messagebox.showinfo("Hinweis", "Erst analysieren.")
            return
        out_dir = filedialog.askdirectory(title="Ausgabeordner")
        if not out_dir:
            return
        os.makedirs(out_dir, exist_ok=True)
        self._log(f"Exportiere {len(self._blocks)} Blöcke nach {out_dir}")
        for b in self._blocks:
            self._export_block(b, out_dir)
        self._log("Fertig.")


if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except Exception as e:
        import traceback
        err_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lin_analyzer_error.txt")
        with open(err_path, 'w') as f:
            traceback.print_exc(file=f)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("Startfehler", f"{e}\n\nDetails in:\n{err_path}")
            root.destroy()
        except:
            pass
