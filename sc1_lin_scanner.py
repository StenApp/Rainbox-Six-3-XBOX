import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import struct
import re
import os
import zlib
from pathlib import Path
from collections import defaultdict


# ── Konstanten ────────────────────────────────────────────────────────────────

UE_MAGIC = b'\xc1\x83\x2a\x9e'

SOUND_INDICATORS   = {'Sound', 'SoundGroup', 'AmbientSound', 'Music'}
TEXTURE_INDICATORS = {'USize', 'VSize', 'Palette', 'MipZero', 'bAlphaTexture',
                      'InternalTime', 'Format', 'VClamp'}
MAP_INDICATORS     = {'LevelInfo', 'MyLevel', 'ReachSpec', 'Zone', 'Model',
                      'Polys', 'Brush'}
ANIM_INDICATORS    = {'AnimNotify', 'FootStepLeft', 'B_L_Wrist_A', 'frame',
                      'R_Weapon_Bone', 'Bip01'}
MESH_INDICATORS    = {'KMeshProps', 'StaticMeshVertex', 'RawTriangles'}

SYSTEM_NAMES = {
    'None', 'Engine', 'Package', 'Class', 'Core', 'Object', 'System',
    'InternalTime', 'SurfaceType', 'Color', 'VClamp', 'Format', 'USize',
    'VSize', 'Shader', 'StaticMesh', 'Texture', 'Window', 'Console',
    'Sound', 'Vector', 'Rotator', 'Zone', 'Location', 'Brush', 'Model',
    'Polys', 'Actor', 'Pawn', 'Light', 'Mover', 'LevelInfo', 'ZoneInfo',
}

KNOWN_NAMES = {
    'FisherVoice', 'FisherFoley', 'GunShots', 'GunShotsB',
    'GuardVoice', 'GuardFoley', 'CommonSounds', 'CommonMusic',
    'Exspetsnaz', 'CIA', 'PoliceVoice', 'GeorgiaPolice',
    'SwissBankGuard', 'ChineseSoldier', 'KoreanSoldier',
    'TerroristVoice', 'CiaBureaucratFemale', 'Hamlet',
    'Grinko', 'Masse', 'Mitchell', 'Lambert', 'Grimsdottir',
    'EchelonTextures', 'EchelonFont', 'EchelonAnimations', 'SamFisher',
    'Echelon', 'EchelonCharacter', 'EchelonHUD', 'EchelonEffect',
    'EchelonIngredient', 'EchelonPattern', 'EchelonGameObject',
}


# ── Kern-Logik ────────────────────────────────────────────────────────────────

def decompress_lin(data: bytes) -> tuple[bytes, bool]:
    chunks, offset = [], 0
    while offset + 8 <= len(data):
        uncomp = struct.unpack_from('<I', data, offset)[0]
        comp   = struct.unpack_from('<I', data, offset + 4)[0]
        if comp == 0 or uncomp == 0:
            break
        if offset + 8 + comp > len(data):
            break
        chunk = data[offset + 8: offset + 8 + comp]
        if chunk[:2] not in (b'\x78\x9c', b'\x78\xda', b'\x78\x01', b'\x78\x5e'):
            break
        try:
            dec = zlib.decompress(chunk)
            if len(dec) != uncomp:
                break
            chunks.append(dec)
            offset += 8 + comp
        except zlib.error:
            break
    if chunks:
        return b''.join(chunks), True
    return data, False


def read_names(data: bytes, pkg_start: int) -> list[str]:
    try:
        nc   = struct.unpack_from('<I', data, pkg_start + 12)[0]
        noff = struct.unpack_from('<I', data, pkg_start + 16)[0]
        if nc > 100000 or noff > 0x500000:
            return []
        pos, names = pkg_start + noff, []
        for _ in range(min(nc, 5000)):
            if pos >= len(data):
                break
            slen = data[pos]; pos += 1
            if slen == 0 or slen > 200:
                break
            raw = data[pos: pos + slen - 1]
            pos += slen + 4
            try:
                names.append(raw.decode('ascii'))
            except UnicodeDecodeError:
                names.append('\x00')
        return names
    except Exception:
        return []


def classify_type(names: list[str]) -> str:
    ns = set(names)
    if ns & SOUND_INDICATORS:   return 'Sound (UAX)'
    if ns & MAP_INDICATORS:     return 'Map (UNR)'
    if ns & MESH_INDICATORS:    return 'StaticMesh (USX)'
    if ns & ANIM_INDICATORS:    return 'Animation (UKX)'
    if ns & TEXTURE_INDICATORS: return 'Texture (UTX)'
    script_hints = {'Function', 'State', 'Struct', 'Enum', 'ByteProperty',
                    'IntProperty', 'BoolProperty', 'FloatProperty'}
    if ns & script_hints:       return 'Script (U)'
    return 'Unknown'


def get_pkg_name(names: list[str]) -> str:
    for nm in names:
        if nm in KNOWN_NAMES:
            return nm
    for nm in names:
        if nm not in SYSTEM_NAMES and len(nm) > 3 \
                and nm[0].isupper() and not nm.startswith('\x00') and nm.isascii():
            return nm
    return ''


def scan_lin_data(data: bytes) -> list[dict]:
    magic_offs = [m.start() for m in re.finditer(re.escape(UE_MAGIC), data)]
    results = []
    for i, pos in enumerate(magic_offs):
        nxt = magic_offs[i + 1] if i + 1 < len(magic_offs) else len(data)
        bsz = nxt - pos
        try:
            ver  = struct.unpack_from('<H', data, pos + 4)[0]
            lic  = struct.unpack_from('<H', data, pos + 6)[0]
            nc   = struct.unpack_from('<I', data, pos + 12)[0]
            ec   = struct.unpack_from('<I', data, pos + 20)[0]
            eoff = struct.unpack_from('<I', data, pos + 24)[0]
            ic   = struct.unpack_from('<I', data, pos + 28)[0]
            ioff = struct.unpack_from('<I', data, pos + 32)[0]
        except struct.error:
            continue
        standalone = (eoff < bsz) and (ioff < bsz)
        names      = read_names(data, pos)
        clean      = [n for n in names if not n.startswith('\x00')]
        results.append({
            'index':      i,
            'offset':     pos,
            'size':       bsz,
            'version':    ver,
            'licensee':   lic,
            'exp_count':  ec,
            'imp_count':  ic,
            'standalone': standalone,
            'pkg_type':   classify_type(clean),
            'pkg_name':   get_pkg_name(clean),
        })
    return results


def build_report(lin_path: str, packages: list[dict],
                 compressed: bool, raw_size: int, dec_size: int) -> str:
    lines = []
    name = Path(lin_path).name
    lines.append(f'LIN-DATEI: {name}')
    lines.append(f'Quelle:    {lin_path}')
    if compressed:
        lines.append(f'Größe:     {raw_size:,} bytes (komprimiert) → {dec_size:,} bytes entpackt')
    else:
        lines.append(f'Größe:     {raw_size:,} bytes (bereits entpackt)')
    sa   = sum(1 for p in packages if p['standalone'])
    stub = len(packages) - sa
    lines.append(f'Packages:  {len(packages)} gefunden  ({sa} eigenständig / {stub} Stubs)')
    lines.append('')

    # Typ-Übersicht
    by_type = defaultdict(int)
    for p in packages:
        by_type[p['pkg_type']] += 1
    lines.append('TYPEN:')
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f'  {t:<22} {c:>3}')
    lines.append('')

    # Tabelle
    lines.append(f'{"#":>3}  {"Offset":>10}  {"Größe":>9}  {"SA":>3}  {"Typ":<22}  Name')
    lines.append('─' * 80)
    for p in packages:
        sa_str   = '✓' if p['standalone'] else '✗'
        name_str = p['pkg_name'] or f'[ec={p["exp_count"]}]'
        lines.append(f'{p["index"]:>3}  {p["offset"]:#10x}  {p["size"]:>9,}  '
                     f'{sa_str:>3}  {p["pkg_type"]:<22}  {name_str}')

    # Eigenständige
    sa_list = [p for p in packages if p['standalone']]
    if sa_list:
        lines.append('')
        lines.append(f'EIGENSTÄNDIG EXTRAHIERBAR ({len(sa_list)}):')
        for p in sa_list:
            lines.append(f'  {(p["pkg_name"] or "?"):<35}  {p["pkg_type"]}  ({p["size"]:,} bytes)')

    # Stubs
    stub_list = [p for p in packages if not p['standalone']]
    if stub_list:
        lines.append('')
        lines.append(f'NUR ALS STUB — LIN-serialisiert ({len(stub_list)}):')
        for p in stub_list:
            lines.append(f'  {(p["pkg_name"] or "?"):<35}  {p["pkg_type"]}  ({p["size"]:,} bytes)')

    return '\n'.join(lines)


def process_lin(src_path: str, out_dir: str, log) -> bool:
    try:
        with open(src_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        log(f'  FEHLER  {Path(src_path).name}: {e}')
        return False

    data, compressed = decompress_lin(raw)
    packages         = scan_lin_data(data)
    report_text      = build_report(src_path, packages, compressed, len(raw), len(data))

    out_path = os.path.join(out_dir, Path(src_path).stem + '_scan.txt')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report_text)

    sa   = sum(1 for p in packages if p['standalone'])
    stub = len(packages) - sa
    comp = f'{len(raw):,} → {len(data):,} bytes' if compressed else f'{len(raw):,} bytes'
    log(f'  OK    {Path(src_path).name:<45}  {len(packages):>3} pkgs  ({sa} SA / {stub} Stub)  {comp}')
    return True


def write_summary(all_results: list[dict], out_path: str) -> None:
    lines = []
    lines.append('SC1 LIN SCANNER — GESAMTÜBERSICHT')
    lines.append('=' * 70)
    lines.append(f'LIN-Dateien: {len(all_results)}')

    total_pkgs = sum(r['pkg_count'] for r in all_results)
    total_sa   = sum(r['sa_count']  for r in all_results)
    lines.append(f'Packages:    {total_pkgs}  ({total_sa} eigenständig / {total_pkgs - total_sa} Stubs)')
    lines.append('')

    # Alle Package-Namen dedupliziert
    all_names: dict[str, dict] = {}
    for r in all_results:
        lin_name = Path(r['lin']).name
        for p in r['packages']:
            nm = p['pkg_name']
            if not nm:
                continue
            if nm not in all_names:
                all_names[nm] = {'type': p['pkg_type'], 'count': 0,
                                 'sa': 0, 'lins': []}
            all_names[nm]['count'] += 1
            if p['standalone']:
                all_names[nm]['sa'] += 1
            if lin_name not in all_names[nm]['lins']:
                all_names[nm]['lins'].append(lin_name)

    by_type: dict[str, list] = defaultdict(list)
    for nm, info in sorted(all_names.items()):
        by_type[info['type']].append((nm, info))

    lines.append('ALLE PACKAGES NACH TYP:')
    lines.append('=' * 70)
    for t in sorted(by_type.keys()):
        entries = by_type[t]
        lines.append(f'\n{t} ({len(entries)}):')
        lines.append(f'  {"Name":<35}  {"SA":>3}  {"Inst":>4}  LINs')
        lines.append(f'  {"─"*35}  {"─"*3}  {"─"*4}  {"─"*30}')
        for nm, info in sorted(entries):
            sa_str   = '✓' if info['sa'] > 0 else '✗'
            lins_str = ', '.join(info['lins'][:3])
            if len(info['lins']) > 3:
                lins_str += f' +{len(info["lins"])-3}'
            lines.append(f'  {nm:<35}  {sa_str:>3}  {info["count"]:>4}  {lins_str}')

    lines.append('')
    lines.append('=' * 70)
    lines.append('PRO LIN-DATEI:')
    lines.append(f'  {"LIN":<45}  {"Pkgs":>4}  {"SA":>4}  {"Stub":>4}')
    lines.append(f'  {"─"*45}  {"─"*4}  {"─"*4}  {"─"*4}')
    for r in sorted(all_results, key=lambda x: Path(x['lin']).name):
        lines.append(f'  {Path(r["lin"]).name:<45}  {r["pkg_count"]:>4}  '
                     f'{r["sa_count"]:>4}  {r["pkg_count"]-r["sa_count"]:>4}')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SC1 LIN Scanner')
        self.resizable(True, True)
        self.minsize(620, 500)
        self._build()
        self._results = []

    def _build(self):
        pad = dict(padx=8, pady=4)

        # Modus
        mode_frame = ttk.LabelFrame(self, text='Modus')
        mode_frame.pack(fill='x', **pad)
        self.mode = tk.StringVar(value='folder')
        ttk.Radiobutton(mode_frame, text='Einzelne Datei', variable=self.mode,
                        value='file',   command=self._on_mode).pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(mode_frame, text='Ordner',         variable=self.mode,
                        value='folder', command=self._on_mode).pack(side='left', padx=8, pady=4)
        ttk.Radiobutton(mode_frame, text='Rekursiv',       variable=self.mode,
                        value='recursive', command=self._on_mode).pack(side='left', padx=8, pady=4)

        # Eingabe
        in_frame = ttk.LabelFrame(self, text='Eingabe')
        in_frame.pack(fill='x', **pad)
        in_frame.columnconfigure(1, weight=1)
        self.input_label = ttk.Label(in_frame, text='Ordner:')
        self.input_label.grid(row=0, column=0, sticky='w', padx=6, pady=4)
        self.input_var = tk.StringVar()
        ttk.Entry(in_frame, textvariable=self.input_var).grid(
            row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(in_frame, text='…', width=3,
                   command=self._browse_input).grid(row=0, column=2, padx=4, pady=4)

        # Filter
        self.filter_frame = ttk.Frame(in_frame)
        self.filter_frame.grid(row=1, column=0, columnspan=3, sticky='w', padx=6, pady=2)
        ttk.Label(self.filter_frame, text='Filter:').pack(side='left')
        self.filter_var = tk.StringVar(value='*.lin')
        ttk.Entry(self.filter_frame, textvariable=self.filter_var, width=14).pack(
            side='left', padx=4)

        # Ausgabe
        out_frame = ttk.LabelFrame(self, text='Ausgabeordner (für TXT-Berichte)')
        out_frame.pack(fill='x', **pad)
        out_frame.columnconfigure(1, weight=1)
        self.out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.out_var).grid(
            row=0, column=1, sticky='ew', padx=4, pady=4)
        ttk.Button(out_frame, text='…', width=3,
                   command=self._browse_output).grid(row=0, column=2, padx=4, pady=4)

        # Optionen
        opt_frame = ttk.Frame(self)
        opt_frame.pack(fill='x', padx=8, pady=2)
        self.summary_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text='Gesamtübersicht schreiben (_summary.txt)',
                        variable=self.summary_var).pack(side='left')

        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=6)
        ttk.Button(btn_frame, text='Scannen', command=self._run).pack(side='left', padx=4)
        ttk.Button(btn_frame, text='Log leeren', command=self._clear_log).pack(side='left', padx=4)

        # Log
        log_frame = ttk.LabelFrame(self, text='Log')
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
        mode = self.mode.get()
        self.input_label.config(text='Datei:' if mode == 'file' else 'Ordner:')
        self.input_var.set('')

    def _browse_input(self):
        if self.mode.get() == 'file':
            p = filedialog.askopenfilename(title='LIN-Datei wählen',
                                           filetypes=[('LIN-Dateien', '*.lin'), ('Alle', '*')])
        else:
            p = filedialog.askdirectory(title='Ordner wählen')
        if p:
            self.input_var.set(p)
            base = os.path.dirname(p) if self.mode.get() == 'file' else p
            self.out_var.set(os.path.join(base, '_reports'))

    def _browse_output(self):
        p = filedialog.askdirectory(title='Ausgabeordner wählen')
        if p:
            self.out_var.set(p)

    def _log(self, msg: str):
        self.log_text.config(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.config(state='disabled')
        self.update_idletasks()

    def _clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

    def _collect_files(self) -> list[str]:
        mode  = self.mode.get()
        src   = self.input_var.get().strip()
        filt  = self.filter_var.get().strip() or '*.lin'
        import fnmatch

        if mode == 'file':
            return [src] if os.path.isfile(src) else []
        elif mode == 'folder':
            if not os.path.isdir(src):
                return []
            return sorted(
                os.path.join(src, f) for f in os.listdir(src)
                if fnmatch.fnmatch(f.lower(), filt.lower())
                and os.path.isfile(os.path.join(src, f))
            )
        else:  # recursive
            result = []
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for fname in sorted(filenames):
                    if fnmatch.fnmatch(fname.lower(), filt.lower()):
                        result.append(os.path.join(dirpath, fname))
            return result

    def _run(self):
        src     = self.input_var.get().strip()
        out_dir = self.out_var.get().strip()

        if not src or not out_dir:
            messagebox.showwarning('Fehler', 'Eingabe und Ausgabeordner angeben.')
            return

        files = self._collect_files()
        if not files:
            messagebox.showwarning('Fehler', 'Keine passenden Dateien gefunden.')
            return

        os.makedirs(out_dir, exist_ok=True)
        self._log(f'{len(files)} LIN-Datei(en) gefunden\n')

        self._results = []
        ok = skip = 0

        for lin_path in files:
            # Verarbeiten
            try:
                with open(lin_path, 'rb') as f:
                    raw = f.read()
            except OSError as e:
                self._log(f'  FEHLER  {Path(lin_path).name}: {e}')
                skip += 1
                continue

            data, compressed = decompress_lin(raw)
            packages         = scan_lin_data(data)
            report_text      = build_report(lin_path, packages, compressed,
                                            len(raw), len(data))

            out_path = os.path.join(out_dir, Path(lin_path).stem + '_scan.txt')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(report_text)

            sa   = sum(1 for p in packages if p['standalone'])
            stub = len(packages) - sa
            comp = (f'{len(raw):,} → {len(data):,} bytes'
                    if compressed else f'{len(raw):,} bytes')
            self._log(f'  OK    {Path(lin_path).name:<40}  '
                      f'{len(packages):>3} pkgs  ({sa} SA / {stub} Stub)  {comp}')

            self._results.append({
                'lin':       lin_path,
                'packages':  packages,
                'pkg_count': len(packages),
                'sa_count':  sa,
            })
            ok += 1

        # Gesamtübersicht
        if self.summary_var.get() and self._results:
            summary_path = os.path.join(out_dir, '_summary.txt')
            write_summary(self._results, summary_path)
            self._log(f'\nGesamtübersicht: {summary_path}')

        self._log(f'\nFertig — {ok} gescannt, {skip} Fehler.')
        self._log(f'Berichte: {out_dir}')


if __name__ == '__main__':
    app = App()
    app.mainloop()
