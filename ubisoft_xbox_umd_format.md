# Ubisoft Xbox UMD Format Documentation
## Version 2 — Rainbow Six 3 / Splinter Cell series (Original Xbox)

**Reverse engineered:** StenApp / Claude, May 2026  
**Research basis:** landaire (https://landaire.net/a-file-format-uncracked-for-20-years/)  
**Known files:** `xboxufiles.umd`, `xboxdynamic.umd`, `dynamicxbox.umd` (SC1)

---

## Overview

The `.umd` file is Ubisoft's adaptation of Epic's UMOD installer format, repurposed as a
read-only asset container for Xbox titles built on Unreal Engine 2. It serves as a flat
archive of files that the engine can load directly by offset — in contrast to the companion
`.lin` files which contain a serialized linear I/O stream requiring exact load-order replay.

Two UMD files are present per game:

| File | Contents |
|---|---|
| `xboxufiles.umd` | Compiled UnrealScript packages (`.u`) — engine and game logic |
| `xboxdynamic.umd` | Sound headers (`.uax`), mission templates (`.tpt`), configs (`.ini`), localization (`.int/.deu/.fra/.ita/.esp`), and misc data |

Files with `offset == 0` in the TOC are **not stored here** — they live in the companion
`Common.lin` stream (e.g. `Core.u` in `xboxufiles.umd`).

---

## File Layout

```
[File data region]
    offset 0x000000 .. toc_offset-1
    Contains raw file data, concatenated, with 16-byte alignment padding between entries.
    Each extractable entry's data begins at its TOC-stated offset.
    Standard UE2 packages begin with magic bytes: C1 83 2A 9E (0x9E2A83C1).

[TOC region]
    offset toc_offset .. toc_end-1
    Contains the file count and TOC entries (see below).

[Footer]
    offset toc_end .. toc_end+19  (= last 20 bytes of file)
    Fixed 20-byte structure (see below).
```

---

## Footer Structure (last 20 bytes)

```c
struct UMD_Footer {
    uint8_t  magic[4];      // A3 C5 E3 9F  (Unreal UMOD magic = 0x9FE3C5A3 LE)
    uint32_t toc_offset;    // Offset of TOC from start of file
    uint32_t toc_end;       // Offset of footer = filesize - 20
    uint32_t version;       // Always 2 in observed files
    uint32_t crc32;         // CRC32 of file data from offset 0 to toc_offset
};
```

**Example — xboxufiles.umd:**
```
A3 C5 E3 9F  67 78 52 00  57 7A 52 00  02 00 00 00  17 17 3F C8
magic        toc_off      toc_end      version=2    crc32
             0x00527867   0x00527A57
```

**Example — xboxdynamic.umd:**
```
A3 C5 E3 9F  45 90 22 00  44 E4 22 00  02 00 00 00  E3 AE DE C3
magic        toc_off      toc_end      version=2    crc32
             0x00229045   0x0022E444
```

---

## TOC Structure

The TOC begins immediately at `toc_offset`.

### File Count

```c
compact_int file_count;   // UE2 variable-length encoded integer (see below)
```

### TOC Entry (repeated file_count times)

```c
struct TOC_Entry {
    compact_int name_len;   // Length of name string INCLUDING null terminator
    char        name[name_len]; // Null-terminated filename, backslash path separator
    uint32_t    offset;     // Byte offset of file data from start of UMD.
                            // 0 = file is stored in companion Common.lin, not here.
    uint32_t    size;       // Size of file data in bytes
    uint32_t    flags;      // Always 0 in observed files. Purpose unknown.
};
```

Total entry size = variable (1–2 bytes for name_len) + name_len bytes + 12 bytes.

---

## UE2 Compact Integer Encoding

Used for `file_count` and `name_len`. Same as standard Unreal Engine 2 compact int:

```
Byte 0:  bit 7 = sign (1 = negative)
         bit 6 = continuation (1 = more bytes follow)
         bits 5..0 = value bits 5..0

Byte 1 (if byte 0 bit 6 set):
         bit 7 = continuation
         bits 6..0 = value bits 12..6

Byte 2 (if byte 1 bit 7 set):
         bit 7 = continuation
         bits 6..0 = value bits 19..13

... up to 5 bytes total.
```

**Examples:**
```
0x0E        → 14  (single byte, no continuation, not negative)
0x1A        → 26  (single byte)
0x57 0x08   → 535 (two bytes: 0x57 = cont+23, 0x08 = 512 → 23+512=535)
```

---

## Observed Contents

### xboxufiles.umd (R6:3 Xbox — 5.278 KiB, version 2)

| # | Filename | Offset | Size | Note |
|---|---|---|---|---|
| 1 | `System\Core.u` | 0 | 74.175 | **In Common.lin** |
| 2 | `System\Engine.u` | 0x000121C0 | 1.319.162 | UE2 package |
| 3 | `System\Gameplay.u` | 0x001542C0 | 19.076 | UE2 package |
| 4 | `System\IpDrv.u` | 0x00158D50 | 20.913 | UE2 package |
| 5 | `System\R61stWeapons.u` | 0x0015DF10 | 118.200 | UE2 package |
| 6 | `System\R63rdWeapons.u` | 0x0017ACD0 | 36.842 | UE2 package |
| 7 | `System\R6Abstract.u` | 0x00183CC0 | 75.791 | UE2 package |
| 8 | `System\R6Characters.u` | 0x001964D0 | 59.857 | UE2 package |
| 9 | `System\R6Engine.u` | 0x001A4EB0 | 2.427.959 | UE2 package |
| 10 | `System\R6Game.u` | 0x003F5AF0 | 418.733 | UE2 package |
| 11 | `System\R6Gameplay.u` | 0x0045BEA0 | 255.049 | UE2 package |
| 12 | `System\R6SFX.u` | 0x0049A2F0 | 229.230 | UE2 package |
| 13 | `System\R6Weapons.u` | 0x004D2260 | 340.928 | UE2 package |
| 14 | `System\R6WeaponGadgets.u` | 0x00525620 | 3.967 | UE2 package |
| 15 | `System\XBOXLive.u` | 0x005265A0 | 4.807 | UE2 package |

**Implication for Common.lin:** Only `Core.u` is in the lin stream for R6:3.
All other scripts load directly from this UMD, bypassing the lin serialization.
This differs from SC1 where the entire script warmup is embedded in `common.lin`.

### xboxdynamic.umd (R6:3 Xbox — 2.233 KiB, version 2)

535 total entries. Summary by type:

| Extension | Count | Extractable | Notes |
|---|---|---|---|
| `.uax` | 272 | 272 | Sound package headers (data in `.SB2`/`.SS2` files) |
| `.tpt` | 115 | 114 | Mission team templates (1 in Common.lin) |
| `.ini` | 38 | 38 | Map and system configs (`[Engine.R6MissionDescription]` etc.) |
| `.int` | 37 | 37 | English localization |
| `.fra` | 17 | 17 | French localization |
| `.deu` | 17 | 17 | German localization |
| `.ita` | 17 | 17 | Italian localization |
| `.esp` | 17 | 17 | Spanish localization |
| `.xsr` | 3 | 3 | Xbox Voice Recognition banks |
| `.sp2` | 1 | 1 | `Sounds\MacArthur.SP2` (single audio clip) |
| `.ka` | 1 | 1 | `KarmaData\ragdoll.ka` (Karma physics ragdoll) |

Notable entry: `Maps\_debug.ini` — references map name `rooms`, a debug/test map
not present in the standard maps listing.

---

## Relation to Common.lin

The UMD and `.lin` formats work together:

```
xboxufiles.umd   → direct file access → Engine.u, R6Engine.u, R6Game.u ... (14 scripts)
                                                                              ↑
                                         Core.u is the ONLY script in Common.lin
                                         (offset=0 in TOC → fetched from lin stream)

xboxdynamic.umd  → direct file access → .uax headers, .tpt, .ini, .int, ...
                 → 1 entry in lin     → Template\Air-H-G3A3-G.tpt (offset=0)

Common.lin       → sequential stream  → Core.u + map-specific UE2 packages
MapName.lin      → sequential stream  → map geometry, textures, static meshes, scripts
```

The engine resolves file requests by:
1. Checking the UMD TOC by filename
2. If found with offset > 0: read directly from UMD at stated offset
3. If found with offset = 0: read from the current lin stream position
4. If not in UMD: read from lin stream

---

## Differences from Standard UMOD

| Feature | Standard UMOD | Ubisoft Xbox UMD |
|---|---|---|
| Purpose | Installer package | Read-only runtime asset container |
| Footer magic | `9F E3 C5 A3` | Same |
| Entry fields | name, offset, size, type, unk1, unk2 (5 uint32s) | name, offset, size, flags (3 uint32s) |
| Name encoding | compact_int length + string | compact_int length + string (same) |
| File count | compact_int | compact_int (same) |
| Offset=0 meaning | Invalid/deleted | File stored in companion .lin stream |
| Version | 1 | 2 |

The key structural difference is **3 uint32s per entry instead of 5** — the Open Season
QuickBMS scripts that use `get TYPE long / get unk1 long / get unk2 long` will read 8 bytes
too many per entry and go off-track.

---

## 010 Editor Template Sketch

```c
typedef struct {
    uint8   name_len_raw;   // compact int byte 0
    // (if name_len_raw & 0x40) uint8 name_len_ext; // compact int byte 1
    char    name[/* decoded name_len */];
    uint32  offset;         // 0 = in companion .lin
    uint32  size;
    uint32  flags;          // always 0
} TOC_ENTRY;

typedef struct {
    TOC_ENTRY entries[/* file_count */];
} TOC;

typedef struct {
    char    magic[4];       // A3 C5 E3 9F
    uint32  toc_offset;
    uint32  toc_end;
    uint32  version;        // 2
    uint32  crc32;
} FOOTER;
```
