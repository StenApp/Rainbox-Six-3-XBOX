"""
RS3 Xbox LIN Scanner
--------------------
Scannt alle LIN-Dateien eines RS3-Verzeichnisses und erstellt ein Inventar:

Für jede LIN-Datei:
  - Welche Unreal Packages sind enthalten
  - Klassifizierung: extern vorhanden / nur in LIN / extern+LIN
  - Eigenständig extrahierbar (eoff/ioff im Block) oder Stub

Klassifizierung basiert auf Linear.ini:
  [Exception]-Typen  → extern (UMD/Disc), NICHT in LIN
  [Copy]-Dateien     → extern (Disc) UND in LIN
  Alle anderen       → NUR in LIN (map-spezifisch)

Verwendung:
  python rs3_lin_scanner.py --game <RS3-Ordner> [--linear <Linear.ini>] [--output <report.txt>]
  python rs3_lin_scanner.py --lin <einzelne.lin>
"""

import struct
import re
import os
import sys
import zlib
import argparse
from pathlib import Path
from collections import defaultdict


# ── Konstanten ────────────────────────────────────────────────────────────────

UE_MAGIC   = b'\xc1\x83\x2a\x9e'

# RS3 Linear.ini [Exception]: diese Typen werden NICHT linearisiert
DEFAULT_EXCEPTION_EXTS = {
    'tpt', 'ini', 'bik', 'raw', 'umd', 'uax', 'sp2', 'u', 'xsr', 'tga',
    'int', 'fra', 'deu', 'ita', 'esp', 'jap', 'kor', 'por',
}

# RS3 Linear.ini [Copy]: diese Dateien existieren extern UND werden linearisiert
DEFAULT_COPY_FILES = {
    # Animations
    'r61stassault_ukx', 'r61stgrenade_ukx', 'r61sthands_ukx', 'r61stitems_ukx',
    'r61stlmg_ukx', 'r61stpistol_ukx', 'r61stshotgun_ukx', 'r61stsniper_ukx',
    'r61stsub_ukx',
    # StaticMeshes
    'r61stweapons_sm', 'r63rdweapons_sm', 'r6sfx_sm',
    # Textures
    'inventory_t', 'r61stweapons_t', 'r63rdweapons_t', 'r6sfx_t',
}

# Mapping: Dateierweiterung → Unreal Package Typ
EXT_TO_TYPE = {
    'rsm': 'Map',
    'utx': 'Textures',
    'ukx': 'Animations',
    'usx': 'StaticMeshes',
    'uax': 'Sounds',
    'u':   'Script',
    'ka':  'KarmaData',
}


# ── Linear.ini Parser ─────────────────────────────────────────────────────────

def parse_linear_ini(path: str) -> tuple[set, set]:
    """
    Parst Linear.ini und gibt zurück:
      exception_exts: set of lowercase extensions (ohne Punkt)
      copy_files:     set of lowercase Dateinamen (ohne Extension)
    """
    exception_exts = set()
    copy_files     = set()
    section        = None

    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            if line.startswith('['):
                section = line.strip('[]').lower()
                continue
            if section == 'exception':
                if line.lower().startswith('ext='):
                    exception_exts.add(line[4:].strip().lower())
            elif section == 'copy':
                if line.lower().startswith('file='):
                    # File=(Src="Animations\R61stAssault_UKX.ukx",mode=2)
                    # oder File=(Src="...")
                    m = re.search(r'Src="([^"]+)"', line, re.IGNORECASE)
                    if m:
                        src = m.group(1)
                        fname = Path(src).stem.lower()
                        copy_files.add(fname)

    return exception_exts, copy_files


# ── UMD Inventar ──────────────────────────────────────────────────────────────

def parse_umd_listing(path: str) -> set[str]:
    """
    Liest ein QuickBMS-Output-Listing einer UMD-Datei und gibt
    alle Dateinamen (lowercase, ohne Pfad) zurück.
    """
    names = set()
    if not os.path.isfile(path):
        return names
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            # Format: "  0000000000000000 74175      System\Core.u"
            parts = line.strip().split()
            if len(parts) >= 3 and re.match(r'^[0-9a-f]{16}$', parts[0]):
                fname = Path(parts[2].replace('\\', '/')).stem.lower()
                names.add(fname)
    return names


# ── LIN Dekompression ─────────────────────────────────────────────────────────

def decompress_lin(data: bytes) -> bytes:
    """
    RS3-Format: [uint32 uncomp][uint32 comp][zlib_data] × N Chunks.
    Gibt dekomprimierten Stream zurück, oder Rohdaten wenn kein zlib.
    """
    chunks = []
    offset = 0
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
        return b''.join(chunks)
    # Keine zlib-Chunks gefunden -> Datei ist bereits dekomprimiert (z.B. Common.lin)
    return data


# ── Unreal Package Parsing ────────────────────────────────────────────────────

def read_compact(data: bytes, pos: int) -> tuple[int, int]:
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
    return (-val if neg else val), pos


def read_names(data: bytes, pkg_start: int, max_names: int = 5000) -> list[str]:
    """Liest Name-Table eines Unreal Packages. Gibt nur saubere ASCII-Namen zurück."""
    try:
        nc   = struct.unpack_from('<I', data, pkg_start + 12)[0]
        noff = struct.unpack_from('<I', data, pkg_start + 16)[0]
        if nc > 100000 or noff > 0x200000:
            return []
        pos = pkg_start + noff
        names = []
        for _ in range(min(nc, max_names)):
            if pos >= len(data):
                break
            slen = data[pos]; pos += 1
            if slen == 0 or slen > 200:
                break
            raw = data[pos: pos + slen - 1]
            pos += slen + 4
            try:
                nm = raw.decode('ascii')
                names.append(nm)
            except UnicodeDecodeError:
                # Name-Table ist hier durch LIN-Serialisierung kaputt
                names.append('\x00CORRUPT\x00')
        return names
    except Exception:
        return []


def read_import_packages(data: bytes, pkg_start: int, names: list[str]) -> set[str]:
    """
    Liest Import-Table und gibt externe Package-Namen zurück
    (nur Top-Level-Imports mit outer == 0).
    """
    try:
        ic   = struct.unpack_from('<I', data, pkg_start + 28)[0]
        ioff = struct.unpack_from('<I', data, pkg_start + 32)[0]
        if ic > 50000 or ioff > 0x500000:
            return set()
        pos = pkg_start + ioff
        pkgs = set()
        for _ in range(min(ic, 10000)):
            if pos >= len(data):
                break
            pkg_idx, pos = read_compact(data, pos)
            cls_idx, pos = read_compact(data, pos)
            outer = struct.unpack_from('<i', data, pos)[0]; pos += 4
            name_idx, pos = read_compact(data, pos)
            if outer == 0 and 0 <= pkg_idx < len(names):
                nm = names[pkg_idx]
                if nm and nm not in ('Core', 'Engine', 'None', '') \
                        and len(nm) > 1 and not nm.startswith('\x00'):
                    pkgs.add(nm)
        return pkgs
    except Exception:
        return set()


def scan_lin(data: bytes) -> list[dict]:
    """
    Scannt dekomprimierten LIN-Datenstrom nach Unreal Packages.
    Gibt Liste von Package-Dicts zurück.
    """
    results     = []
    magic_offs  = [m.start() for m in re.finditer(re.escape(UE_MAGIC), data)]

    for i, pos in enumerate(magic_offs):
        nxt = magic_offs[i + 1] if i + 1 < len(magic_offs) else len(data)
        bsz = nxt - pos

        try:
            ver  = struct.unpack_from('<H', data, pos + 4)[0]
            lic  = struct.unpack_from('<H', data, pos + 6)[0]
            nc   = struct.unpack_from('<I', data, pos + 12)[0]
            noff = struct.unpack_from('<I', data, pos + 16)[0]
            ec   = struct.unpack_from('<I', data, pos + 20)[0]
            eoff = struct.unpack_from('<I', data, pos + 24)[0]
            ic   = struct.unpack_from('<I', data, pos + 28)[0]
            ioff = struct.unpack_from('<I', data, pos + 32)[0]
        except struct.error:
            continue

        standalone = (eoff < bsz) and (ioff < bsz)
        names      = read_names(data, pos)
        clean_names = [n for n in names if not n.startswith('\x00')]
        ext_pkgs   = read_import_packages(data, pos, clean_names)

        # Package-Typ aus Name-Table erraten
        pkg_type = _guess_type(clean_names, ext_pkgs)
        pkg_name = _guess_name(clean_names, ext_pkgs)

        results.append({
            'index':      i,
            'offset':     pos,
            'size':       bsz,
            'version':    ver,
            'licensee':   lic,
            'name_count': nc,
            'exp_count':  ec,
            'imp_count':  ic,
            'standalone': standalone,
            'names':      clean_names,
            'ext_pkgs':   ext_pkgs,
            'pkg_type':   pkg_type,
            'pkg_name':   pkg_name,
        })

    return results


# ── Heuristiken ───────────────────────────────────────────────────────────────

_SYSTEM_NAMES = {
    'None', 'Engine', 'Package', 'Class', 'Core', 'Object', 'System',
    'InternalTime', 'SurfaceType', 'Color', 'VClamp', 'Format', 'USize',
    'VSize', 'Shader', 'StaticMesh', 'Texture', 'Window', 'Console',
    'Sound', 'Vector', 'Rotator', 'Zone', 'Location',
}

_KNOWN_PKG_NAMES = {
    'R6Engine', 'R6Game', 'R6Gameplay', 'R6Abstract', 'R6Characters',
    'R6Weapons', 'R61stWeapons', 'R63rdWeapons', 'R6SFX', 'R6WeaponGadgets',
    'XBOXLive', 'IpDrv', 'Gameplay', 'R6Planning', 'R6Common', 'R6Menu',
    'R6Missions', 'R6TexturesReticule', 'R6TexturesHUD', 'R6TexturesCommon',
}

def _guess_type(names: list[str], ext_pkgs: set[str]) -> str:
    ns = set(names)
    if 'LevelInfo' in ns or 'MyLevel' in ns or 'ReachSpec' in ns:
        return 'Map'
    if 'KMeshProps' in ns or 'KMeshProps0' in ns:
        return 'StaticMeshes'
    if any(n.startswith('AnimNotify') for n in names[:30]):
        return 'Animations'
    if 'R6 R Hand' in ns or 'FootStepLeft' in ns or 'B_L_Wrist_A' in ns:
        return 'Animations'
    if 'frame' in ns and any('Bolt' in n or 'Magazine' in n for n in names):
        return 'Animations'
    if 'USize' in ns and 'VSize' in ns and 'Palette' in ns:
        return 'Textures'
    if 'MipZero' in ns or 'bAlphaTexture' in ns:
        return 'Textures'
    if any(n in _KNOWN_PKG_NAMES for n in names[:20]):
        return 'Script'
    return 'Unknown'


def _guess_name(names: list[str], ext_pkgs: set[str]) -> str:
    # Bekannte Package-Namen direkt in der Name-Table?
    for nm in names:
        if nm in _KNOWN_PKG_NAMES:
            return nm
    # Aus externen Refs
    useful = sorted(p for p in ext_pkgs
                    if p not in _SYSTEM_NAMES and len(p) > 2)
    if useful:
        return f'[refs: {", ".join(useful[:3])}]'
    return ''


# ── Klassifizierung ───────────────────────────────────────────────────────────

def classify_package(pkg: dict,
                     exception_exts: set[str],
                     copy_files: set[str],
                     umd_contents: set[str]) -> str:
    """
    Gibt Klassifizierung zurück:
      'exception'  - Extension in [Exception] → extern, nicht in LIN
      'copy'       - Dateiname in [Copy] → extern + in LIN
      'umd'        - in UMD gefunden → extern + in LIN
      'lin_only'   - nur in LIN vorhanden
    """
    name_lower = pkg['pkg_name'].lower().lstrip('[').rstrip(']')

    # UMD-Check
    for nm in [pkg['pkg_name']] + list(pkg['ext_pkgs']):
        if nm.lower() in umd_contents:
            return 'umd'

    # Copy-Check
    if name_lower in copy_files:
        return 'copy'
    for ext_pkg in pkg['ext_pkgs']:
        if ext_pkg.lower() in copy_files:
            return 'copy'

    # Typ-basierte Exception-Check
    type_to_ext = {
        'Sounds':      'uax',
        'Script':      'u',
        'Map':         'rsm',
        'Textures':    'utx',
        'Animations':  'ukx',
        'StaticMeshes':'usx',
    }
    ext = type_to_ext.get(pkg['pkg_type'], '')
    if ext and ext in exception_exts:
        return 'exception'

    return 'lin_only'


# ── Hauptfunktion ─────────────────────────────────────────────────────────────

def scan_directory(game_dir: str,
                   linear_ini: str | None = None,
                   umd_listings: list[str] | None = None,
                   output_path: str | None = None,
                   verbose: bool = False) -> None:

    # Linear.ini laden
    if linear_ini and os.path.isfile(linear_ini):
        exception_exts, copy_files = parse_linear_ini(linear_ini)
        print(f'Linear.ini: {len(exception_exts)} Exception-Typen, {len(copy_files)} Copy-Dateien')
    else:
        exception_exts = DEFAULT_EXCEPTION_EXTS
        copy_files     = DEFAULT_COPY_FILES
        print('Linear.ini nicht gefunden, verwende Standardwerte')

    # UMD-Listings laden
    umd_contents: set[str] = set()
    for listing in (umd_listings or []):
        umd_contents |= parse_umd_listing(listing)
    if umd_contents:
        print(f'UMD-Inventar: {len(umd_contents)} Einträge')

    # Alle LIN-Dateien finden
    lin_files = sorted(Path(game_dir).rglob('*.lin'))
    if not lin_files:
        # Auch ohne Rekursion direkt im Ordner
        lin_files = sorted(Path(game_dir).glob('*.lin'))
    print(f'LIN-Dateien gefunden: {len(lin_files)}')
    print()

    # Report-Output vorbereiten
    lines_out = []
    def out(s=''):
        lines_out.append(s)
        print(s)

    # Gesamt-Statistik
    total_pkgs    = 0
    total_standalone = 0
    type_stats    = defaultdict(int)
    class_stats   = defaultdict(int)
    lin_only_pkgs = []  # (lin_file, pkg) für "nur in LIN" Liste

    for lin_path in lin_files:
        out(f'{'─'*70}')
        out(f'LIN: {lin_path.name}  ({lin_path.stat().st_size:,} bytes komprimiert)')

        with open(lin_path, 'rb') as f:
            raw = f.read()

        decompressed = decompress_lin(raw)
        if len(decompressed) != len(raw):
            out(f'     → dekomprimiert: {len(decompressed):,} bytes')

        packages = scan_lin(decompressed)
        out(f'     → {len(packages)} Packages gefunden')

        standalone_count = sum(1 for p in packages if p['standalone'])
        stub_count       = len(packages) - standalone_count
        out(f'        Eigenständig: {standalone_count}   Stubs: {stub_count}')
        out()

        if not packages:
            continue

        # Pakete ausgeben
        out(f'  {"#":>3}  {"Offset":>10}  {"Größe":>8}  {"SA":>3}  {"Typ":>12}  {"Klasse":>10}  Name')
        out(f'  {"─"*3}  {"─"*10}  {"─"*8}  {"─"*3}  {"─"*12}  {"─"*10}  {"─"*30}')

        for pkg in packages:
            cls = classify_package(pkg, exception_exts, copy_files, umd_contents)
            sa  = '✓' if pkg['standalone'] else '✗'

            cls_labels = {
                'exception': 'EXTERN',
                'copy':      'EXT+LIN',
                'umd':       'EXT+UMD',
                'lin_only':  'NUR-LIN',
            }
            cls_str = cls_labels.get(cls, cls)

            out(f'  {pkg["index"]:>3}  {pkg["offset"]:#10x}  {pkg["size"]:>8,}  {sa:>3}  '
                f'{pkg["pkg_type"]:>12}  {cls_str:>10}  {pkg["pkg_name"]}')

            type_stats[pkg['pkg_type']]  += 1
            class_stats[cls]             += 1
            total_pkgs                   += 1
            if pkg['standalone']:
                total_standalone += 1
            if cls == 'lin_only':
                lin_only_pkgs.append((lin_path.name, pkg))

        out()

    # Gesamtübersicht
    out('═' * 70)
    out('GESAMTÜBERSICHT')
    out('═' * 70)
    out(f'LIN-Dateien:       {len(lin_files)}')
    out(f'Packages gesamt:   {total_pkgs}')
    out(f'  Eigenständig:    {total_standalone}')
    out(f'  Stubs:           {total_pkgs - total_standalone}')
    out()
    out('Nach Typ:')
    for t, c in sorted(type_stats.items(), key=lambda x: -x[1]):
        out(f'  {t:>14}: {c:>4}')
    out()
    out('Nach Verfügbarkeit:')
    cls_labels = {'exception':'Extern (Exception)','copy':'Extern+LIN (Copy)',
                  'umd':'Extern+UMD','lin_only':'NUR in LIN'}
    for cls, c in sorted(class_stats.items(), key=lambda x: -x[1]):
        out(f'  {cls_labels.get(cls,cls):>24}: {c:>4}')
    out()

    if lin_only_pkgs:
        out('═' * 70)
        out(f'PACKAGES NUR IN LIN ({len(lin_only_pkgs)}) — kein externes Gegenstück:')
        out('═' * 70)
        for lin_name, pkg in lin_only_pkgs:
            sa = '✓ standalone' if pkg['standalone'] else '✗ stub'
            out(f'  {lin_name:30s}  {pkg["pkg_type"]:12}  {sa:14}  {pkg["pkg_name"]}')

    # Output schreiben
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines_out))
        print(f'\nReport gespeichert: {output_path}')


def scan_single_lin(lin_path: str,
                    linear_ini: str | None = None,
                    umd_listings: list[str] | None = None) -> None:
    """Scannt eine einzelne LIN-Datei."""
    exception_exts = DEFAULT_EXCEPTION_EXTS
    copy_files     = DEFAULT_COPY_FILES

    if linear_ini and os.path.isfile(linear_ini):
        exception_exts, copy_files = parse_linear_ini(linear_ini)

    umd_contents: set[str] = set()
    for listing in (umd_listings or []):
        umd_contents |= parse_umd_listing(listing)

    with open(lin_path, 'rb') as f:
        raw = f.read()

    data = decompress_lin(raw)
    compressed = len(data) != len(raw)

    print(f'LIN: {lin_path}')
    print(f'Größe: {len(raw):,} bytes{"  (zlib-komprimiert, entpackt: " + f"{len(data):,})" if compressed else "  (bereits entpackt)"}')
    print()

    packages = scan_lin(data)
    print(f'{len(packages)} Packages:')
    print()
    print(f'  {"#":>3}  {"Offset":>10}  {"Größe":>8}  {"SA":>3}  {"Typ":>12}  '
          f'{"Klasse":>10}  Name')
    print(f'  {"─"*3}  {"─"*10}  {"─"*8}  {"─"*3}  {"─"*12}  {"─"*10}  {"─"*30}')

    for pkg in packages:
        cls    = classify_package(pkg, exception_exts, copy_files, umd_contents)
        sa     = '✓' if pkg['standalone'] else '✗'
        cls_lbl = {'exception':'EXTERN','copy':'EXT+LIN',
                   'umd':'EXT+UMD','lin_only':'NUR-LIN'}.get(cls, cls)
        print(f'  {pkg["index"]:>3}  {pkg["offset"]:#10x}  {pkg["size"]:>8,}  {sa:>3}  '
              f'{pkg["pkg_type"]:>12}  {cls_lbl:>10}  {pkg["pkg_name"]}')


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='RS3 Xbox LIN Scanner — Inventarisiert Packages in LIN-Dateien',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--game', '-g', metavar='ORDNER',
                        help='RS3-Spielverzeichnis (rekursiv nach .lin suchen)')
    parser.add_argument('--lin', '-l', metavar='DATEI',
                        help='Einzelne LIN-Datei scannen')
    parser.add_argument('--linear', metavar='Linear.ini',
                        help='Pfad zur Linear.ini (Standard: Standardwerte)')
    parser.add_argument('--umd', action='append', metavar='listing.txt',
                        help='QuickBMS-Listing einer UMD-Datei (wiederholbar)')
    parser.add_argument('--output', '-o', metavar='report.txt',
                        help='Report in Datei speichern')
    parser.add_argument('--verbose', '-v', action='store_true')

    args = parser.parse_args()

    if not args.game and not args.lin:
        parser.print_help()
        sys.exit(1)

    if args.lin:
        scan_single_lin(args.lin, args.linear, args.umd)
    else:
        scan_directory(args.game, args.linear, args.umd, args.output, args.verbose)


if __name__ == '__main__':
    main()
