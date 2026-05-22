# UE2OffsetDump — Rainbow Six 3 (Xbox) Address Documentation

## Overview

UE2OffsetDump is an xemu plugin that intercepts the Unreal Engine 2 package loader
at runtime. It hooks into specific points in the game executable to record which
bytes in a `.lin` file belong to which Unreal package. The output is used by
`unrealin` to statically extract embedded packages from `.lin` files.

All addresses are for the **Rainbow Six 3 (Germany) (En,Fr,De,Es,It)** Xbox release.
XBE base address: `0x00010000`. Identified via Ghidra with the XboxDev ghidra-xbe loader.

---

## Hook Points

### ULINKER_CTOR — `0x002e5cd0`

**Function:** `FUN_002e5cd0`

The ULinkerLoad constructor. Called whenever the engine creates a new package linker
object — i.e. when a new Unreal package begins loading from the `.lin` stream.
This is the entry point for the hook that signals "a new package starts here".

The function initializes the linker object fields and writes two vtable pointers:
- `[EBP+0x00]` → `PTR_LAB_004591a8` (ULinkerLoad vtable)
- `[EDI+0x00]` → `PTR_LAB_00459148` (FArchive vtable)

### ULINKER_END — `0x002e641e`

The RET instruction at the end of `FUN_002e5cd0`. Marks the point where the
ULinkerLoad constructor has fully completed. Used to signal "package header
has been read, serial data follows".

### PRELOAD_ENTRY — `0x002e5cd0`

Same function as ULINKER_CTOR. In RS3, the constructor and the initial preload
are combined — the function both constructs the linker object and reads the
package header (name table, import table, export table) in one pass.

---

## Archive / Stream Layout

### ARCHIVE_TO_FS_OFF — `0x40`

The vtable offset used to call `Seek()` on the underlying file archive object.

```asm
002e622c  CALL dword ptr [EDX + 0x40]   ; Seek(SerialOffset)
```

Called via:
```asm
002e6220  MOV  ECX, dword ptr [EDI + 0x458]   ; get archive object
002e6226  MOV  EAX, dword ptr [EBP + 0x48]    ; SerialOffset
002e6229  MOV  EDX, dword ptr [ECX]            ; load vtable
002e622b  PUSH EAX
002e622c  CALL dword ptr [EDX + 0x40]          ; vtable Seek()
```

Compare: SC1 uses `0x34` for the same vtable offset. RS3 vtable layout differs.

### ARCHIVE_POSITION_OFFSET — `0x458`

Offset within the linker object (`EDI`) that holds a pointer to the underlying
`FArchive` (the actual file reader). The vtable of this object is then used
for `Seek()` and `Read()` calls.

```asm
MOV ECX, dword ptr [EDI + 0x458]   ; get FArchive pointer from linker
MOV EDX, dword ptr [ECX]           ; load vtable
CALL dword ptr [EDX + 0x40]        ; call Seek()
```

### FILE_READ vtable offset — `0x08`

Vtable offset for `Read()` on the FArchive object:

```asm
002e61ea  CALL dword ptr [EDX + 0x08]   ; Read(buffer, size)
```

### FILE_SEEK vtable offset — `0x40`

Same as ARCHIVE_TO_FS_OFF. The Seek() call uses vtable offset `0x40`.

---

## Export Table Fields (stack offsets within FUN_002e5cd0)

These are the offsets on the stack frame (`EBP`-relative) where export table
fields are stored during the preload loop:

| Field        | Stack offset | Notes                                      |
|--------------|--------------|--------------------------------------------|
| SerialSize   | `[EBP+0x44]` | Size in bytes of the exported object data  |
| SerialOffset | `[EBP+0x48]` | Byte offset in the stream to seek to       |

The seek sequence at `0x002e6215`–`0x002e622c`:
```asm
002e6215  MOV EAX, [EBP+0x44]          ; SerialSize — check if > 0
002e621a  JLE LAB_002e62b8             ; skip if no data
002e6220  MOV ECX, [EDI+0x458]         ; get FArchive*
002e6226  MOV EAX, [EBP+0x48]          ; SerialOffset
002e6229  MOV EDX, [ECX]               ; vtable
002e622b  PUSH EAX                     ; push SerialOffset as seek argument
002e622c  CALL [EDX+0x40]              ; Seek(SerialOffset)
```

---

## Comparison: SC1 vs RS3

| Constant              | SC1        | RS3        | Notes                        |
|-----------------------|------------|------------|------------------------------|
| ULINKER_CTOR          | 0x0003A3A2 | 0x002e5cd0 | RS3 code is much larger      |
| ULINKER_END           | 0x0003AA65 | 0x002e641e |                              |
| PRELOAD_ENTRY         | 0x000383F6 | 0x002e5cd0 | Combined in RS3              |
| CREATE_FILE_READER    | 0x00016FD0 | 0x002f478c | suspected — no-op seek, see notes |
| FILE_READ             | 0x00017837 | TBD        | vtable offset 0x08 confirmed |
| FILE_SEEK             | 0x0001C82D | TBD        | vtable offset 0x40 confirmed |
| ARCHIVE_TO_FS_OFF1    | 0x34       | 0x40       | vtable layout differs        |
| ARCHIVE_TO_FS_OFF2    | 0x2C       | TBD        |                              |
| ARCHIVE_TO_FS_OFF3    | 0x34       | 0x40       |                              |
| ARCHIVE_POSITION_OFF  | 0x40       | 0x458      | Linker→FArchive pointer      |
| PRELOAD_FINISH        | 0x00038464 | 0x002e641e | Same as ULINKER_END in RS3    |
| CREATE_EXPORT_ADDED   | 0x00039517 | 0x002e63ed | RS3 uses EBP not ESI — needs hook change |
| STATIC_LOAD_OBJECT    | 0x0004E7DA | n/a        | Not applicable — no StaticLoadObject in RS3 |
| XLAUNCH_NEW_IMAGE_A   | 0x00176243 | n/a        | Not applicable — RS3 uses internal map loading |
| NAMES_MAP             | 0x033834C  | 0x0050d408 | Global FName array           |

---

## `.lin` Loading Call Chain (RS3)

```
FUN_0001fa40           — main startup / level loader
  └─ FUN_002f46e0      — .lin file opener (opens file handle, calls FUN_002f4350)
       └─ FUN_002f4350 — cache validator (checks file timestamps/sizes)
       └─ FUN_002e5cd0 — ULinkerLoad constructor + header reader  ← HOOK HERE
            └─ FUN_002e46e0  — called at start (base class init)
            └─ [EDI+0x458]   — FArchive vtable (Seek/Read)
```

The `.lin` files are opened via a vtable call `[EDX+0x10]` on a file manager
object (stub at `FUN_00052dc0`, actual implementation set at runtime).
The `Linear_*.ini` files in `System\` define `SRC=` / `DST=` / `MODE=` mappings
that control which `.lin` files are loaded for each map.

---

## Hook Applicability Analysis (RS3)

| Hook                    | Needed | RS3 Status | Notes |
|-------------------------|--------|------------|-------|
| ULINKER_CTOR            | ✅ yes | ✅ found   | Primary hook — signals new package start |
| ULINKER_END             | ✅ yes | ✅ found   | Signals package header fully read |
| PRELOAD_ENTRY           | ✅ yes | ✅ found   | Same as ULINKER_CTOR in RS3 |
| NAMES_MAP               | ✅ yes | ✅ found   | Needed for CREATE_EXPORT_ADDED + STATIC_LOAD_OBJECT |
| CREATE_FILE_READER      | ✅ yes | ⚠️ suspected | Pairs filename with FArchive; RS3 uses vtable call, not standard path |
| CREATE_EXPORT_ADDED     | ✅ yes | 🔍 TBD     | Reads FName via NAMES_MAP — needed for export tracking |
| STATIC_LOAD_OBJECT      | ✅ yes | 🔍 TBD     | Reads package name via NAMES_MAP — needed for dynamic loads |
| FILE_READ               | ✅ yes | 🔍 TBD     | Tracks which FArchive is active — needed |
| FILE_SEEK               | ⚠️ problem | ❌ no-op | **Seek is RET 0x4 in RS3 — this hook will never fire.** Splice boundary detection via seek offsets does not work. Package boundaries must be detected via ULINKER_CTOR alone. This is a fundamental difference from SC1 and requires plugin changes for RS3. |
| ARCHIVE_TO_FS_OFF       | ✅ yes | ✅ found   | vtable offset 0x40 |
| ARCHIVE_POSITION_OFFSET | ✅ yes | ✅ found   | 0x458 |

---

## Still Missing / Not Applicable

The following addresses still need to be located in Ghidra:

- `NAMES_MAP` — **found: `0x0050d408`** (global FName array, base address in `.data`)
- `CREATE_FILE_READER_ENTRY` — **suspected: `0x002f478c`** (vtable call `[EAX+0x10]` in `FUN_002f46e0`
  where DST filename in EDI is paired with the FArchive instance; RS3 does not use a standard
  CreateFileReader call chain — .lin seek is a no-op (`RET 0x4`), so the standard
  compressed/windows reader split likely does not apply)
- `CREATE_FILE_READER_WINDOWS` — **suspected: same as ENTRY or not applicable** (no-op seek architecture)
- `CREATE_FILE_READER_COMPRESSED` — **suspected: not applicable** (lin stream is not seekable)
- `STATIC_LOAD_OBJECT` — UObject::StaticLoadObject
- `CREATE_EXPORT_ADDED` — hook point after export is added to object table
- `ARCHIVE_TO_FS_OFF2` — second vtable walk offset (SC1: 0x2C)

- `PRELOAD_FINISH` — **`0x002e641e`** (same as ULINKER_END — constructor and preload are combined in RS3)
- `CREATE_EXPORT_ADDED` — **`0x002e63ed`** (`MOV dword ptr [ECX + ESI*4], EBP` — note: RS3 uses EBP for new object, not ESI as in SC1; hook code needs adjustment)
- `STATIC_LOAD_OBJECT` — **not applicable** — no `StaticLoadObject` string found in RS3 XBE; RS3 uses `DynamicLoadObject` instead; hook is optional (only affects object load order logging)
- `XLAUNCH_NEW_IMAGE_A` — **not applicable** — RS3 does not call `XLaunchNewImageA` for map changes; level loading is handled internally via the UE2 loader without Xbox relaunch
- `FILE_READ` / `FILE_SEEK` absolute addresses — **TBD** — vtable offsets confirmed (0x08 / 0x40) but absolute entry point addresses not yet located; can be found via XREFs from `[EDI+0x458]` vtable calls in `FUN_002e5cd0`
- `ARCHIVE_TO_FS_OFF2` — **TBD** — SC1 value was 0x2C; may not be needed if RS3 uses a single vtable offset (0x40) for all Seek calls

---

## Tool and Environment

- **Ghidra version:** 12.0.3 with XboxDev ghidra-xbe plugin
- **XBE:** `default.xbe` from Rainbow Six 3 (Germany) Xbox disc
- **XBE size:** 5020 KB, ImageSize 5628 KB
- **Analysis date:** 2026-05-15
