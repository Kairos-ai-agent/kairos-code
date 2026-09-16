---
name: "firmware-reverse-engineering"
description: "Reverse engineer NOR flash firmware dumps from embedded SoCs (ITE IT9866E / SMEDIA02, ARM-based MCU firmware, Allwinner BROM boot, Rockchip boot blobs, ESP32 partitions, etc.). Trigger when the user p"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\firmware-reverse-engineering\\SKILL.md"
---
# Firmware Reverse Engineering

When the user hands you a raw flash dump and says "decompile this" / "看看这个固件" / "extract the boot logo" — this is the workflow. Embedded firmware is **not** a host-platform binary: no PE/ELF headers, often a proprietary container, often a private ISA, often proprietary compression. Set expectations accordingly.

## When to trigger

- User provides a `.bin` / `.hex` / `.flash` file and asks to reverse engineer / decompile / disassemble / analyze
- Device is an embedded SoC (ITE/SMEDIA/IT9866E, Allwinner, Rockchip, Nuvoton, NXP, STM32, ESP32, MIPS-based LCD controllers, ARM-based MCUs)
- User wants to: identify firmware structure, find boot splash image, extract OSD assets, modify UI strings, find embedded config, understand partition layout

**NOT for**: compiled host-platform binaries (use `binary-analysis`), APK/JAR analysis, regular executable decompilation.

## Workflow

### Phase 0: SDK discovery (do this BEFORE triage)

**This is the step that prevents the most common failure mode**: declaring a binary "irreversible" when the vendor's SDK, documentation, and example projects are sitting in a sibling directory the agent never enumerated.

Vendor SDK directories are almost always named after the chip family or vendor:
```bash
# Generic scan — adapt the names to whatever the file path suggests
ls -la firmware-parent-dir/                    # top-level
find firmware-parent-dir -maxdepth 3 -type d   # all subdirs up to 3 levels
find firmware-parent-dir -maxdepth 4 -iname "*sdk*" -o -iname "*toolchain*" -o -iname "*vendor*"
find firmware-parent-dir -maxdepth 4 -iname "CMakeLists.txt" -o -iname "*.cmake"
```

What to look for and what each tells you:
| Found                              | What it means                          |
|------------------------------------|----------------------------------------|
| `ite_sdk2470/`, `stm32cubeide/`, `esp-idf/`, etc. | Vendor SDK — entire architecture, init scripts, build tooling, demo projects |
| `CMakeLists.txt` or `build.sh`     | Build system reveals ISA, compiler, target, sample projects you can compare against |
| `materials/`, `datasheet/`, `doc/` | Datasheets + reference manuals — pin counts, register maps, boot flow |
| `教学资源/` (or similar locale-named dirs) | Training videos, IDE tutorials — often leaked by vendor to OEM customers |
| `.vscode/tasks.json` or `launch.json` | Real working build commands the OEM uses — copy these |

**Signal that an SDK is present**: file size and structure suggest a vendor-style SDK (multi-GB `ite_sdk2470/` directories, 100+ demo projects, doc/html/ subdirectory). If you see this, the binary is almost certainly **buildable from source using vendor tools** rather than requiring a full RE effort.

The 2026-08-10 IT9866E case missed an entire `ite_sdk2470/` SDK (build/, doc/, project/, sdk/, tool/, win32/) because the agent only looked at the flash dump and not at the user's working directory tree. A 5-minute `find` scan would have revealed `mkrom.exe`, `DEMO9860_RGB_800x480.txt`, `project/test_lcd/test_lcd.c` — everything needed to write new firmware without RE.

### Phase 0.5: User pushback signal

If the user re-asks "能不能反编译" / "如果一定要做呢" / "有没有办法" after you concluded "can't reverse", that is **feedback that your prior conclusion was incomplete**, not pressure to defend it. Re-trigger Phase 0 (SDK discovery) and look harder. Common blind spots:

- SDK / toolchain in sibling directory (Phase 0 fix)
- Companion storage (SD card, NAND) the code is loaded from at runtime
- Web interface for sniffing runtime behavior instead of static RE
- Per-chip key derivation that you assumed was static but is dynamic

Document what you found that you missed last time, then act on it.

### Phase 1: Triage (always do this first)

```bash
file firmware.bin          # often just "data" — that's expected
xxd firmware.bin | head -40
sha256sum firmware.bin
stat firmware.bin          # size tells you which NOR chip was used
```

Look at the first 256 bytes manually with xxd. Identify:
- **Magic** at offset 0: `SMEDIA02`, `eGON`, `BOOT`, `0x27051956` (uImage), `0x7F ELF`, etc.
- **Copyright / build string** near offset 0x40: "(c) 2009 ITE Tech.", "Allwinner", etc.
- **Endianness**: if first few words look sensible as one mode but not the other, that's the SoC's endian
- **File size**: 4 MB / 8 MB / 16 MB / 32 MB = typical NOR flash capacities; 1 MB / 2 MB / 4 MB = typical SPI flash

### Phase 2: Header & Partition Map

Many embedded firmwares are **multi-partition** (boot + app + assets + language tables). Find ALL header instances:

```python
import struct
data = open("firmware.bin", "rb").read()
N = len(data)
hdrs = []
i = 0
while i < N - 8:
    if data[i:i+8] == b"SMEDIA02":  # or your magic
        hdrs.append(i)
        i += 4  # skip past header
    else:
        i += 1
# Partitions: hdr[k] .. hdr[k+1]
```

Decode each header's fields (offset, size, load-addr, entry-point, build timestamp). Dump to `regions.txt`.

### Phase 3: Signature Scan (don't rely on binwalk)

**binwalk v2.1.0 has import errors on some Python installs** (`ModuleNotFoundError: No module named 'binwalk.core'`). Skip it. Write a 30-line manual scanner:

```python
SIGS = [
    (b"\x1f\x8b\x08", "gzip"),
    (b"\xfd\x37\x7a\x58\x5a\x00", "xz"),
    (b"\x5d\x00\x00\x00", "lzma-probable"),  # see pitfall
    (b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"BM", "bmp"),
    (b"\x7fELF", "elf"),
    (b"\x27\x05\x19\x56", "uImage"),
    (b"SMEDIA02", "smedia"),
    (b"hsqs", "squashfs"),
    (b"UBI#", "ubi"),
    (b"JFFS", "jffs2"),
    (b"PK\x03\x04", "zip"),
    # SoC-specific
    (b"eGON", "allwinner-boot"),
    (b"BOOT", "rockchip-boot"),
    (b"ITEDATA", "ite-data"),
    (b"ITEFONT", "ite-font"),
    (b"ITEPNL", "ite-panel"),
]
for needle, name in SIGS:
    locs = [i for i in range(N) if data.startswith(needle, i)]
    if locs:
        print(f"  {name}: {len(locs)} hits, first @ 0x{locs[0]:08x}")
```

**Validate** each hit — see Pitfalls section. Raw counts lie.

### Phase 4: Asset Extraction

Extract the validated assets. Common ones to look for:

**JPEG** (OSD icons, splash, photos):
```python
i = 0
while True:
    s = data.find(b"\xff\xd8\xff", i)
    if s < 0: break
    e = data.find(b"\xff\xd9", s + 3)
    if e > 0 and (e + 2 - s) >= 256:  # skip tiny false matches
        open(f"img_{s:08x}.jpg", "wb").write(data[s:e+2])
        i = e + 2
    else:
        i = s + 1
```

**PNG** (RGBA icons with transparency, splash logos):
```python
s = data.find(b"\x89PNG\r\n\x1a\n")
e = data.find(b"IEND", s)
open(f"img_{s:08x}.png", "wb").write(data[s:e+8])  # 8 = IEND\xae\x42\x60\x82
```

**BMP** (framebuffer data, large UI assets):
```python
s = data.find(b"BM")
fsize = struct.unpack_from("<I", data, s+2)[0]
dibsize = struct.unpack_from("<I", data, s+14)[0]
if 64 <= fsize <= 4*1024*1024 and dibsize in (12, 40, 52, 56, 64, 108, 124):
    open(f"img_{s:08x}.bmp", "wb").write(data[s:s+fsize])
```

**Splash image identification**: the largest JPEG whose dimensions match the panel's native resolution (800x480 = 7" LCD, 1024x600 = 9", 1920x1080 = full HD). Use `file extracted.jpg` to verify.

### Phase 5: String Mining

```python
import re
ascii_strs = [(m.start(), m.group().decode()) for m in re.finditer(rb"[\x20-\x7e]{6,}", data)]
utf16_strs = [(m.start(), m.group().decode("utf-16-le", "replace")) for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data)]
```

Look for these high-signal keywords to identify firmware purpose:

| Domain | Keywords |
|---|---|
| Display / OSD | Brightness, Contrast, Volume, Language, Mode, Menu, Picture, Sound, Source, HDMI, VGA, Aspect, Resolution, Screensaver, Audio, Channel, Default, Mute, Backlight, Hue, Saturation, Sharpness, Tint |
| Web UI | `<title>`, `<span>`, `.cgi?action=`, `<input type=`, `var X = document`, jQuery / `n.fn.extend` |
| Debug / Build | TIMEOUT, ERR:, Dump, version, build, enable, compress, size |
| Driver symbols | USB, SD, MMC, I2C, SPI, GPIO, DMA, Interrupt, OHCI, EHCI, Bulk |
| Storage | NorFlash, SPIFlash, NAND, Sector, Block, ECC |

### Phase 5.5: ERASED-FLASH CHECK (do this BEFORE disassembly)

**Critical pitfall**: NOR flash is **erased to 0xFF** before programming. Regions that were never written look identical to low-entropy plaintext code. The IT9866E case missed **11.5 MB of erased flash (72% of a 16 MB image)** because entropy alone can't tell plaintext from 0xFF.

```python
from collections import Counter
def shannon_entropy(buf):
    c = Counter(buf); total = len(buf)
    return -sum((v/total)*math.log2(v/total) for v in c.values()) if total else 0

def classify_block(buf):
    if not buf: return "empty"
    if buf[0] == 0xff and buf.count(0xff) > len(buf) * 0.95:
        return "ERASED"      # empty flash, NOT code
    if buf[0] == 0x00 and buf.count(0x00) > len(buf) * 0.95:
        return "ZEROS"
    e = shannon_entropy(buf)
    if e > 7.5: return "COMPRESSED/ENCRYPTED"
    if e > 6.5: return "weakly-compressed"
    if e > 5.0: return "binary"
    return "low-entropy"      # could be code OR constant tables
```

Build the map at 64 KB or 256 KB granularity first, identify ERASED regions, then focus on the non-erased subset only. Often 60-80% of a flash dump is empty.

### Phase 6: Disassembly Attempt (with realistic expectations)
### Phase 6: Disassembly Attempt (with realistic expectations)

Try each ISA, region-by-region. Modern SoCs are often multi-core — test exhaustively before concluding "private ISA":

```python
import capstone
modes = [
    ("MIPS32 LE", capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN),
    ("MIPS32 BE", capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_BIG_ENDIAN),
    ("ARM LE", capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM | capstone.CS_MODE_LITTLE_ENDIAN),
    ("THUMB LE", capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN),
    ("THUMB BE", capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_BIG_ENDIAN),
    ("RISC-V 32 LE", capstone.CS_ARCH_RISCV, capstone.CS_MODE_RISCV32),
    ("RISC-V 32 BE", capstone.CS_ARCH_RISCV, capstone.CS_MODE_RISCV32 | capstone.CS_MODE_BIG_ENDIAN),
    ("RISC-V 32C", capstone.CS_ARCH_RISCV, capstone.CS_MODE_RISCV32 | capstone.CS_MODE_RISCVC),
    ("PPC BE", capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN),
    ("X86", capstone.CS_ARCH_X86, capstone.CS_MODE_32),
]
```

For modern multi-core SoCs (ITE IT9866E has ARM + Andes RISC-V + SMEDIA32; many many Allwinner and Rockchip chips have multiple ISAs across cores), you must test all of:
- ARM LE + Thumb LE (32-bit SoC application code)
- RISC-V 32 + RISC-V 32C (Andes, SiFive, Nuclei extensions — `CS_MODE_RISCVC`)
- MIPS32 LE + BE (legacy MCU / older SoCs)

**Don't stop at ARM** — even if vendor docs say "ARM core", there are often DSP / co-processor cores running RISC-V or proprietary ISA whose code shares the flash.

**Three distinct diagnostic outcomes from disassembly attempts**:

1. **Real code in correct ISA**: capstone produces hundreds of valid instructions in a coherent stream. Branches land on plausible targets. PC-relative data loads reference nearby literal pools. → **You're done with architecture discovery; proceed to Ghidra for control flow recovery.**

2. **Code in wrong ISA**: capstone produces lots of valid-looking instructions but jumps land at absurd addresses (outside the partition, or pointing into erased regions), and there's no recognizable function structure (no push/pop pairs, no sp references). → **Wrong ISA / wrong endianness. Re-test with the other endian or another architecture.**

3. **Code NOT present**: capstone produces 0-3 instructions in a 16+ KB window, then stops on invalid bytes. ALL tested ISAs behave the same way. → **The application code is not in this flash dump at all.** Don't conclude "encrypted". See "Code not present" pitfall below.

**Important correction**: ITE IT9866E/IT9854 series uses an **ARM926EJ-S core** (ARM926EJ-S datasheet), not MIPS. The boot ROM is ITE-proprietary ISA, but if any application code exists in the dump it would be ARM (Thumb or Thumb-2). Don't waste cycles testing MIPS on these — test ARM LE/Thumb LE/RISC-V instead. The earlier IT9866E analysis script testing MIPS was looking at the wrong architecture.

**Quantify ISA exhaustion** instead of "few instructions":
- Real code in correct ISA: typically **>50 instructions per 512 B** of coherent stream
- Random data decoded as any ISA: typically **<10 instructions per MB** before capstone stops
- **If all standard ISAs give <10 insns/MB → code is NOT in this region** (private ISA boot table, erased flash, or external-storage loading — see Phase 0 / "code not present" pitfall)

Test script:
```python
import capstone
ISAS = [
    ("ARM LE", capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM | capstone.CS_MODE_LITTLE_ENDIAN),
    ("Thumb LE", capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN),
    ("RISC-V 32 LE", capstone.CS_ARCH_RISCV, capstone.CS_MODE_RISCV32),
    ("MIPS32 LE", capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN),
    ("MIPS32 BE", capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_BIG_ENDIAN),
]
chunk = data[start:start+0x10000]  # 64 KB
for name, arch, mode in ISAS:
    md = capstone.Cs(arch, mode)
    n = sum(1 for _ in md.disasm(chunk, start))  # capstone stops on bad bytes
    print(f"{name:<12}: {n} instructions decoded (16 KB window)")
```

For real MIPS code, hunt for prologues:
- `lui $gp, 0xXXXX` = `0x3c1cXXXX` (LE)
- `addiu $sp, $sp, -N` = `0x27bd????` (LE)
- `jr $ra` = `0x03e00008` (LE)

For ARM Thumb:
- `push {regs, lr}` = `0xe92d....` or `0xb5xx` (16-bit)
- `pop {regs, pc}` = `0xe8bd....` or `0xbdxx`

For RISC-V:
- `auipc` opcode 0x17, `lui` opcode 0x37, `jal` opcode 0x6F, `jalr` opcode 0x67, `beq` opcode 0x63
- Look at the lowest 7 bits of each 32-bit word — distribution skewed toward standard RV opcodes indicates RV code

## Pitfalls

### binwalk v2.1.0 import error
`from binwalk import ...` fails with `ModuleNotFoundError: No module named 'binwalk.core'`. The Python module shipped in binwalk 2.1.0 expects an internal layout that doesn't match what's installed. **Workaround**: write manual signature scan (30 lines), or install the system `binwalk` CLI separately. Don't waste time debugging the import.

### capstone CS_MODE_MIPS16 doesn't exist
Only `CS_MODE_MIPS2`, `CS_MODE_MIPS3`, `CS_MODE_MIPS32`, `CS_MODE_MIPS64` exist. Trying `CS_MODE_MIPS16` raises `AttributeError`. Use MIPS32 instead.

### "5d" byte ≠ LZMA stream
The byte `0x5d` is the LZMA properties byte but appears hundreds of times in normal data. Hits of `b"\x5d\x00"` are **not** LZMA streams. Real LZMA needs full 13-byte header (`props` + `dict_size` LE 4B + `uncompressed_size` LE 8B) followed by a valid compressed payload that `lzma.decompress()` actually decodes. Try all candidate offsets and only trust ones that decompress.

### EDID false positives
The pattern `00 FF FF FF FF FF FF 00` appears many times in firmware as data, but real EDID v1 blocks must have checksum = 0 mod 256 over the 128-byte block. Don't trust the pattern alone — validate checksum.

### Boot splash misidentification
Many JPEGs in firmware. The splash image is the one whose **dimensions match the panel's native resolution**. Other JPEGs are OSD icons (small, maybe RGBA PNG actually), menu thumbs, sample photos. Cross-check with `file extracted.jpg`.

### Private ISA is not your fault
If capstone can't decode the boot region, that's almost always a **proprietary ISA** (ITE, Allwinner BROM, etc.), not a bug in your script. Document this honestly in the report — don't pretend you got disassembly when you didn't.

### The "code not in this dump" diagnostic
**Strong signal that application code is not present in the flash dump** (vs. encrypted/compressed):
- ALL tested ISAs (ARM/Thumb/RISC-V/MIPS/Thumb BE/etc.) give **0-3 instructions** then stop in every region
- Even when scanning random data offsets, capstone produces lots of "valid-looking" instructions but they're isolated — never a coherent stream of hundreds
- The flash dump is mostly erased (0xFF) or non-code regions
- Boot partition contents are entirely the proprietary init script pattern (e.g., ITE's `0xd80000xx 0x002a8802` repeating)

When you see this pattern:
- Do NOT spend cycles trying exotic decompression (the code isn't is)
- Do NOT conclude "encrypted" — first check `0xFF` fill and `0x00` fill
- DO consider: the application code is **loaded at runtime from external storage**
  - SD card (very common — IT9866E SDK has `boot/spi_sd_boot`)
  - NAND flash on a separate chip
  - USB / network boot
  - Companion chip / co-processor

**Verification before declaring "not present"**: extract the boot init scripts, identify the boot ROM's load behavior, and check what storage the SoC supports. The SDK's boot directory usually shows all the load paths. Document this in the report as a real conclusion (the user needs to know where to look next), not a failure.

The 2026-08-10 IT9866E case: 16 MB NOR flash contained 12.8 MB erased + 2 MB resources + 1.94 MB init scripts. Application ARM code was nowhere in the dump — it's on the SD card (the boot ROM loads it after running the init script).

### Compression is almost always proprietary
If standard gzip/xz/lzma/zstd/7z all fail to find valid streams, the firmware uses a vendor-proprietary compression. **Don't waste time** on more exotic algorithms. Note the limitation in the report.

### 0xFF (erased flash) masquerades as plaintext code
Low Shannon entropy doesn't mean code. NOR flash is erased to 0xFF before programming; unprogrammed regions show entropy ≈ 0 because they're uniform. Always check `buf.count(0xff) / len(buf) > 0.95` BEFORE trusting entropy as a "plaintext code" indicator. The IT9866E case had 72% of the file as 0xFF and the prior analysis missed it.

### Prologues don't prove it's code
`push {lr}` = `b5 00` (16-bit) and `push.w {r4-r11, lr}` = `2d e9 f8 4f` (32-bit Thumb-2) appear **naturally in random data**. Scanning for prologue bytes returns hundreds of false positives in 16 MB of firmware. To validate a candidate prologue, disassemble forward 256+ bytes and check if the resulting instructions:
- Form a coherent stream (branches land on plausible targets)
- Have <20% garbage (like `udf`, `bkpt`, or out-of-range branches)
- Reference the stack (`sp` in `ldr`/`str`/`push`/`pop`)

If disassembling 256 B forward gives mostly noise, the prologue was a coincidence.

### Each partition header is followed by the SAME private init table
For SMEDIA02/ITE boot images: every partition's first ~0x80 bytes after the header are the **same** ITE private ISA init sequence (PLL/SDRAM/LCD config). If you disassemble the first partition's init table, you've seen them all. Application code (if any) starts much later — search for it after running the erase-check + entropy map.

### Real control logic may not be in the flash dump at all
After exhausting disassembly options, consider: the C-level control code may be:
- Loaded at runtime from external storage (SD card, USB, network) — **very common for IoT display devices like IT9866E where NOR only contains boot + assets**
- Encrypted with a per-device key (need chip decapsulation)
- Built into a separate companion chip

Before declaring "can't be reversed", verify the firmware's runtime behavior via:
- UART / JTAG log capture (often available on dev boards)
- Web interface calls (if HTTP server is embedded — sniff `/dev/info.cgi`)
- OSD key sequences → menu state machine reconstruction
- Logic analyzer on SPI / I2C / GPIO pins
- **Read the SD card** if the SoC has a card slot — this is where the actual ARM application code usually lives for IoT displays

Runtime sniffing is often faster than static reverse engineering for proprietary firmware.

### Multi-partition confusion
The same magic appearing 4 times in one flash = 4 separate bootable images, not 4 instances of one. Each has its own header and payload. Partitions are bounded by header positions.

## Verification

After extraction, verify each deliverable:

1. **Extracted images open cleanly**: `file images/img_*.jpg` should report valid JPEG/PNG/BMP, not "data"
2. **Splash image dimensions match panel**: read me / LCD datasheet / boot log for native resolution
3. **Strings have context**: keyword hits should cluster around functional code regions, not random data
4. **Disassembly either makes sense or you documented why it doesn't**: don't ship a half-decoded mess — either commit to clean MIPS/ARM output or explicitly say "private ISA, no public disassembly available"

## Deliverables

Generate in same directory as input:
```
analysis/
├── report.md            # Chinese or English summary of findings
├── regions.txt          # Partition map with sizes + header fields
├── strings.txt          # All ASCII + UTF-16 strings with offsets
├── strings-interesting.txt  # Filtered to UI/menu/error/config keywords
├── images/              # Extracted JPEG/PNG/BMP assets
└── code/                # Disassembly attempts (sparse is OK if private ISA)
```

Report structure (Chinese):
1. 文件概要 (size, SHA-256, chip ID)
2. 固件分区表 (partition table)
3. 已识别的硬件寄存器初始化表 (boot init table — say if private ISA)
4. 提取出的资源 (assets — note dimensions, identify splash)
5. 字符串分析 (strings — group by function: OSD / web UI / debug / drivers)
6. 反汇编限制 (disassembly limitations — be honest about proprietary formats)
7. 输出文件清单 (deliverables)
8. 建议 (suggestions — if user wants to modify splash / strings / config, point to offsets)

## References

- `references/smedia02-format.md` — IT9866E / SMEDIA02 boot header structure details
- `references/capstone-pitfalls.md` — capstone API quirks and workarounds for embedded ISA work
- `references/ite-it9866e-mkrom.md` — full mkrom.exe + init.scr syntax, multi-core SoC architecture, standalone ARM build pipeline (no SDK required), SPI flash programming wiring
- `references/embedded-filesystems.md` — finding and extracting embedded FAT12/16 filesystems inside NOR dumps (the IT9866E case held 7 FAT images covering ~14 MB of UI assets, fonts, sounds, web UI, and `.ITU` scene bytecode); when to pivot from disassembly to filesystem mining, ITU scene bytecode structure, LFS detection
- `references/it9866e-case-study.md` — Full transcript of the ITE IT9866E firmware reverse-engineering session (Phase 0 SDK discovery miss, the 72% erased-flash reality, why the "code not in this dump" diagnosis was correct, and the rebuild-from-SDK pivot). Read this before tackling any NOR dump from a board whose chip family you don't recognize.
- `references/ite-itu-scene-format.md` — Partial reverse-engineering notes on the ITE `.ITU` scene-file format (header layout, itcStream-compressed body, widget-type dispatch table, the UCL `ucl.dll` 32-bit import dependency on MSVCR120). Includes a step-by-step verification recipe using the vendor's Windows emulator.

## Absorbed binary-analysis sub-skill

This skill now also covers **analyzing compiled desktop binaries** (`.exe` / `.so` / `.dll` / `.dylib`) to understand internal logic, premium/subscription models, encryption, and auth flow — PE/ELF identification, Flutter Dart AOT analysis, string extraction, config-file examination, and API endpoint discovery. This complements the embedded-firmware (NOR flash dump) focus above; for desktop/binaries use the binary-analysis body. Full body → `references/binary-analysis/`.

## Scripts and Templates

- `scripts/entropy_map.py` — Per-block Shannon entropy probe with 0xFF-erased detection. Run as `python scripts/entropy_map.py firmware.bin`. Outputs a region-by-region classification (ERASED / ZEROS / COMPRESSED-ENCRYPTED / binary / low-entropy) so you can scope disassembly work to the non-erased subset.
- `scripts/isa_probe.py` — Multi-ISA capstone disassembly density comparison. Run as `python scripts/isa_probe.py firmware.bin <offset> <size>`. Tests ARM/Thumb/MIPS/RISC-V/X86 against the chunk and reports instructions decoded per ISA. The "0-3 instructions then stops" signature across all ISAs is the "code not in this dump" indicator.
- `scripts/disasm_libitu.py` — ARM/Thumb disassembler for vendor `.a` libraries built with `-flto`. Skips `.gnu.lto_*` bitcode sections and disassembles the actual `.text.<FuncName>` code. Run with `--rodata` first to see embedded error strings and source paths — usually the cheapest way to map a closed-source library.
- `scripts/disasm_dotnet_dll.py` — MSIL disassembler for the vendor's .NET design-time tools (e.g. ITE `itu.dll`, `DrawrockerGUIDesigner.exe`). Decompiles WidgetLoader-style classes back to IL with token/string resolution.
- `templates/bare-metal-arm/` — Copy-and-modify starting point for rebuilding firmware from scratch without an SDK: `startup.S` (ARM reset vector), `linker.lds` (SDRAM + framebuffer memory map), `main.c` (framebuffer drawing primitives). Build with `arm-none-eabi-gcc -mcpu=arm926ej-s -march=armv5te -marm -ffreestanding -nostdlib`.