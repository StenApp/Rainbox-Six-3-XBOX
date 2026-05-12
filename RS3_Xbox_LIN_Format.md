# Rainbow Six 3 Xbox — `.lin` Format Dokumentation

**Stand:** Mai 2026  
**Referenz-Spiel:** Tom Clancy's Rainbow Six 3 (Xbox, 2003, UE2-Engine, ver=118 lic=21)  
**Verwandte Arbeit:** landaire/unrealin (Splinter Cell 1 Xbox)

---

## 1. Überblick

`.lin`-Dateien sind **serialisierte I/O-Streams** — kein klassisches Container-Format.
Die Xbox-Engine schreibt beim Laden jedes Byte das sie liest 1:1 in die `.lin`-Datei:

```
fn read() {
    let data = self.underlying_read();
    lin_file.write(data);   // alles mitschreiben
    return data;
}
```

Seeks sind **No-Ops** — der Stream kann nur vorwärts gelesen werden.
Dadurch können Bytes doppelt vorkommen wenn die Engine etwas zurücksucht und nochmal liest.

**Konsequenz:** Offsets und Größenangaben im Datei-Header sind **falsch/bedeutungslos**.
Die Daten können nur in exakt der Ladereihenfolge der Engine korrekt deserialisiert werden.

---

## 2. Dateistruktur RS3

### 2.1 Komprimierung

Jede `.lin`-Datei besteht aus mehreren **zlib-komprimierten 16KB-Blöcken**:

```
[block_0: zlib 16KB] [block_1: zlib 16KB] ... [block_n: zlib rest]
```

Dekomprimiert ergibt sich ein **linearer Byte-Stream**.

### 2.2 Dateien auf der Disc

```
System\Common.lin          ← gemeinsame Assets (alle Maps)
System\Airport.lin         ← map-spezifische Assets
System\Airport_multi.lin   ← Multiplayer-Variante
System\Airport_mission_skins.lin
...
Maps\Airport.rsm           ← Map-Geometrie (normales UE2-Package, direkt ladbar)
Maps\Airport.xbx           ← 4KB Konfigurations-Stub
```

Jede `.rsm`-Map hat eine gleichnamige `.lin` in `System\`.

### 2.3 Inhalt Common.lin (dekomprimiert, 3,2 MB)

```
Offset 0x00000:  uint32 = 1             ← Stream-Header/Version
Offset 0x00001:  65536 bytes            ← Textur-Cache (RGBA-Pixeldaten)
Offset 0x10001:  UE2-Package #0         ← Beginn der Package-Sequenz
Offset 0x4735A:  UE2-Package #1
...                                      ← 49 Packages insgesamt
Offset 0x30BD05: Karma-Physics-XML      ← <KARMA><ASSET id="terroskel"...>
Offset 0x31277A: UE2-Package #48        ← letztes Package
Offset 0x314640: Sound-Referenz-Tabellen ← STREAM.SS2, WAV-Referenzen, EOF
```

---

## 3. UE2-Package-Struktur im Stream

### 3.1 Package-Header

Jedes Package beginnt mit dem UE2-Magic `0x9E2A83C1` (LE: `C1 83 2A 9E`).

```
+0x00  magic         uint32  = 0x9E2A83C1
+0x04  version       uint16  = 118  (RS3 Xbox)
+0x06  licensee      uint16  = 21   (RS3 Xbox)
+0x08  pkg_flags     uint32
+0x0C  name_count    uint32
+0x10  name_offset   uint32  ← relativ zum Package-Start
+0x14  export_count  uint32
+0x18  export_offset uint32  ← relativ zum Package-Start
+0x1C  import_count  uint32
+0x20  import_offset uint32  ← relativ zum Package-Start
+0x24  guid[4]       uint32[4]
+0x34  gen_count     uint32
...    generations   GenerationInfo[]
+0x44  name_table    ← erster Name immer 'None'
```

**Wichtig:** Alle Tabellen-Offsets sind **relativ zum jeweiligen Package-Anfang**.
`name_offset = 0x44` ist bei allen 49 Packages identisch (Standard-UE2-Header-Größe).

### 3.2 Abstand zwischen Packages

Die 49 Packages in Common.lin sind **nicht gleichmäßig verteilt** — jedes Package
enthält seine SerialData direkt nach dem Header. Paket-Ende = direkt vor dem
nächsten `C1 83 2A 9E` Magic.

### 3.3 Sonderfall: Geteilte Tabellen

Manche Packages (z.B. Package #30) haben ihre Export-Table **außerhalb** ihrer
eigenen Grenzen — sie zeigen auf Tabellen-Daten die im Stream an einer anderen
Position liegen. Diese Packages sind **nicht eigenständig extrahierbar**.

---

## 4. Bekannte Package-Inhalte (Common.lin)

Aus GNames-Dumps und String-Scans identifiziert:

| Package | Inhalte |
|---------|---------|
| #0 (5811 Namen, 7088 Exports) | Haupt-Asset-Pool: Engine/Core-Objekte, Texturen, StaticMeshes |
| #30 (1063 Exports) | R6Gameplay-Scripts, EventScripts (`R6EVENT_*`) |
| Karma-XML-Block | Ragdoll-Skelett `terroskel` für R6Terrorist.PSK |
| Sound-Tail | STREAM.SS2, HD_Music.SS2, WAV-Referenzen |

---

## 5. Xbox RAM-Layout (beim Laden)

Aus XEMU-Memory-Map (`info mem`) ermittelt:

```
0x00010000  XBE-Header + Init-Data
0x00011000  .text (Code, 3,4 MB, read-only)
0x00381000  .rdata + weitere Sektionen
0x004AA000  .data / Heap
0x82F28000  Haupt-Asset-Heap (7 MB) ← Common.lin wird hier geladen
0x83664000  Weiterer Asset-Heap (2,3 MB)
0x838AE000  Weiterer Asset-Heap (7,3 MB)
```

**Stream-Basisadresse im RAM:** `0x82F28000`
(Alle Package-VAs = `0x82F28000 + stream_offset`)

### 5.1 GNames-Struktur im RAM

```c
struct FNameEntry {
    uint16_t  hash;       // +0x00
    uint16_t  flags;      // +0x02
    uint32_t  index;      // +0x04  ← GNames-Index
    uint32_t  reserved;   // +0x08  = 0
    uint32_t  hash_next;  // +0x0C  ← VA des nächsten Eintrags
    char      name[];     // +0x10  ← ASCII, null-terminated, 4-Byte-aligned
};
```

GNames-Pool bei `0x02070000` enthält beim Map-Load:
- Indizes 0–18679: basis Objekte (Core, Engine, R6-Klassen) — **nicht in unseren Dumps**
- Indizes 18680–20807: Map-spezifische Namen (StaticMeshInstance, R6EVENT_*, Textur-Namen)

---

## 6. Ladereihenfolge (bekannt)

Aus dem XBE-String-Export (`defaukt_xbe_text_export.txt`):

```
1. Common.lin laden (ReaderLoadLinear)
2. Warmup-Objekte deserialisieren (analog SC1 engine_warmup.rs, aber R6-Klassen)
3. Map-spezifische .lin laden
4. MyLevel-Objekt deserialisieren
5. Map-Assets laden
```

**Reihenfolge der Packages in Common.lin** (aus XBE-String-Export):
```
Core.u → Engine.u → Gameplay.u → IpDrv.u →
R6Abstract.u → R6Characters.u → R6Engine.u →
R6Game.u → R6Gameplay.u → R6SFX.u →
R6WeaponGadgets.u → R6Weapons.u → R61stWeapons.u →
R63rdWeapons.u → XBOXLive.u
```

---

## 7. Relevante Adressen in der XBE (default.xbe)

```
BaseAddress:           0x00010000
EntryPoint (retail):   0x0009F067
.text:                 0x00011000 – 0x00381000 (3,4 MB)
.rdata:                0x00381000 – 0x00501000

ReaderSaveLinear:      VA 0x00020290  (referenziert '.u', 'ReaderSaveLinear', 'ReaderLoadLinear')
ReaderLoadLinear:      VA 0x00020290  (gleiche Funktion, beide Strings referenziert)
ULinkerSave-String:    VA 0x00459AB0
Saving-String:         VA 0x00459DFC
FailedSavePrivate:     VA 0x00459D84
```

---

## 8. Extraktion — aktueller Stand

### Was funktioniert
- Zlib-Dekomprimierung der `.lin`-Blöcke ✓
- UE2-Package-Magic-Scan im dekomprimierten Stream ✓
- Name-Table lesen (Namen korrekt) ✓
- Package-Grenzen bestimmen ✓

### Was nicht funktioniert / offen ist
- **Eigenständige Package-Extraktion**: Packages mit geteilten Tabellen (z.B. #30) nicht isoliert extrahierbar
- **Korrekte Deserialisierung**: Load-Order abhängig — ohne RS3-spezifischen Warmup-Trace falsche Ergebnisse
- **SavePackage-Patch**: `SavePackage`-Adresse in XBE nicht gefunden (kein PUSH-Ref auf `ULinkerSave`-String); vermutlich vtable-Aufruf
- **XEMU-Kompatibilität**: RS3 friert nach Ladebalken ein, Map wird nicht vollständig gestartet

### Landaires Ansatz (für SC1, adaptierbar für RS3)
1. QEMU-Plugin aufzeichnet alle I/O-Operationen beim Laden
2. `engine_warmup.rs` definiert die Objekt-Ladereihenfolge bis `MyLevel`
3. Software-Reimplementierung der UE2-Deserializer mit exakter Engine-Semantik
4. Packages werden mit korrekten Offsets/Größen neu serialisiert

**RS3-spezifisches Problem:** Warmup-Liste (`engine_warmup.rs`-Äquivalent) für RS3 fehlt noch.
Muss durch QEMU-Trace oder Reverse Engineering von `game_main` in der XBE ermittelt werden.

---

## 9. UE2OffsetDump — QEMU-Plugin für I/O-Traces

**Repo:** https://github.com/landaire/UE2OffsetDump

Das ist das konkrete Werkzeug um den RS3-Warmup-Trace zu erzeugen. Es ist ein
**XEMU-Plugin** (QEMU Plugin API) das alle Lese- und Seek-Operationen der Engine
mitschreibt und als JSON ausgibt.

### 9.1 Plugin-Output

```json
{
  "file_load_order": [
    "..\\System\\Engine.u",
    "..\\System\\Core.u",
    ...
  ],
  "object_load_order": [
    "Engine.GameEngine",
    "Core.Function",
    ...
  ],
  "raw_io_ops": [
    { "Seek": { "to": 6917, "from": 67715 } },
    { "Read": { "len": 1 } },
    ...
  ]
}
```

- `file_load_order` → welche `.u`/`.utx`/`.uax`-Dateien in welcher Reihenfolge geladen werden
- `object_load_order` → welche Objekte `StaticLoadObject` durchlaufen (= Warmup-Liste)
- `raw_io_ops` → jeder einzelne Read/Seek für exakte Replay-Verifikation

### 9.2 Für RS3 adaptieren

Laut README: neues Spiel durch Anlegen von `crates/plugin/src/games/rs3/` und
Implementierung des `GameDumper`-Traits einbinden. Die generischen
Hook-Templates für Xbox-API und Unreal liegen unter `crate::hooks::*` und
`crate::unreal::*`.

**Voraussetzung:** XEMU mit Plugin-Support bauen (`--enable-plugins`).
Unter Windows entsprechend die MSYS2/MinGW-Build-Pipeline anpassen.

### 9.3 `load-map`-Kommando

Das Plugin hat eine eingebaute Debug-UI mit `load-map`-Befehl:
```
load-map <subdir>/<stem>
```
Damit kann man gezielt eine bestimmte Map laden um deren Trace aufzuzeichnen,
ohne durch das Menü zu navigieren. Für RS3 wäre das z.B.:
```
load-map Airport
```

### 9.4 Workflow für RS3

1. XEMU mit `--enable-plugins` bauen
2. Plugin für RS3 portieren (GameDumper implementieren)
3. RS3 starten, Hauptmenü abwarten → `common.lin`-Trace komplett
4. Für jede Map: `load-map <mapname>` → map-spezifischer Trace
5. Traces als Input für `unrealin`-Äquivalent für RS3 verwenden
6. `unrealin` für RS3 portieren (RS3-spezifische Klassen statt SC1 `Echelon.*`)

---

## 10. Quellen / Referenzen

- landaire, "A File Format Uncracked for 20 Years": https://landaire.net/a-file-format-uncracked-for-20-years/
- EliotVU/Unreal-Library Discussion #134: https://github.com/EliotVU/Unreal-Library/discussions/134
- landaire/unrealin (SC1-Implementierung): https://github.com/landaire/unrealin
- landaire/UE2OffsetDump (QEMU-Plugin für I/O-Traces): https://github.com/landaire/UE2OffsetDump
- `defaukt_xbe_text_export.txt` — String-Export der RS3 XBE (eigene Analyse)
- `Common.lin` — dekomprimierter Stream (eigene Analyse)
