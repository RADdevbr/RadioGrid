"""
macos_bridge.py — camada de isolamento de plataforma do RadioGrid.

Toda a lógica macOS-específica vive aqui. Cada função detecta o sistema
operacional em tempo de execução: no macOS (Darwin) usa a implementação real
(Vision framework via Swift, NSImage para composição, osascript para
notificação, `open -R` para o Finder); em qualquer outro SO (ex.: Linux /
Claude Code) usa um stub seguro que apenas simula o comportamento.

Dessa forma o mesmo arquivo roda sem alterações tanto no ambiente de
desenvolvimento Linux quanto no Mac do usuário final.

O radiogrid.py NUNCA chama subprocess/swift/osascript diretamente — sempre
passa por estas funções.
"""

import os
import re
import shutil
import platform
import tempfile
import unicodedata


# ------------------------------------------------------------------
# Detecção de plataforma
# ------------------------------------------------------------------
def is_macos() -> bool:
    return platform.system() == "Darwin"


# ------------------------------------------------------------------
# Normalização de nome (compartilhada — pura, sem dependência de SO)
# ------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """Remove acentos, faz uppercase e colapsa espaços extras.

    Usada para agrupar imagens do mesmo paciente independente de
    variações de acentuação/espaçamento no OCR.
    """
    if not name:
        return "DESCONHECIDO"
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ASCII", "ignore").decode("ASCII")
    cleaned = " ".join(ascii_name.upper().split())
    return cleaned or "DESCONHECIDO"


# ------------------------------------------------------------------
# OCR — Vision Framework (macOS) / heurística por nome (stub)
# ------------------------------------------------------------------
_OCR_SWIFT_SRC = r"""
import Vision
import Foundation
import AppKit

let imagePath = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: imagePath),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("DESCONHECIDO"); exit(0)
}
let roi = CGRect(x: 0.0, y: 0.85, width: 1.0, height: 0.15)
var result = ""
let req = VNRecognizeTextRequest { r, _ in
    let obs = r.results as? [VNRecognizedTextObservation] ?? []
    result = obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
}
req.recognitionLevel = .accurate
req.regionOfInterest = roi
req.usesLanguageCorrection = false
try? VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([req])
print(result)
"""

_OCR_BINARY_PATH = None


def _get_ocr_binary():
    """Compila o script Swift de OCR uma única vez e cacheia o binário."""
    global _OCR_BINARY_PATH
    if _OCR_BINARY_PATH and os.path.exists(_OCR_BINARY_PATH):
        return _OCR_BINARY_PATH
    import subprocess

    try:
        src_file = tempfile.NamedTemporaryFile(suffix=".swift", delete=False, mode="w")
        src_file.write(_OCR_SWIFT_SRC)
        src_file.close()
        bin_path = src_file.name.replace(".swift", "")
        result = subprocess.run(
            ["swiftc", src_file.name, "-o", bin_path],
            capture_output=True, timeout=60,
        )
        os.unlink(src_file.name)
        if result.returncode == 0:
            _OCR_BINARY_PATH = bin_path
            return bin_path
    except Exception:
        pass
    return None


def _ocr_macos(image_path: str) -> str:
    import subprocess

    binary = _get_ocr_binary()
    if binary is None:
        return "DESCONHECIDO"
    try:
        result = subprocess.run(
            [binary, image_path], capture_output=True, text=True, timeout=15
        )
        raw = result.stdout.strip()
        match = re.match(r"^([A-Za-zÀ-ÿ\s]+)", raw)
        if not match:
            return "DESCONHECIDO"
        return normalize_name(match.group(1).strip())
    except Exception:
        return "DESCONHECIDO"


def _ocr_stub(image_path: str) -> str:
    """Stub Linux: deriva o nome das 2 primeiras palavras do basename.

    Ex.: "JOAO_SILVA_tc_coluna.png" -> "JOAO SILVA".
    Útil para testar o pipeline manualmente sem Vision framework.
    """
    basename = os.path.splitext(os.path.basename(image_path))[0]
    parts = re.split(r"[_\-\s]+", basename)[:2]
    candidate = " ".join(p for p in parts if p)
    if any(c.isdigit() for c in candidate):
        return "DESCONHECIDO"
    normalized = normalize_name(candidate)
    return normalized if len(normalized) > 3 else "DESCONHECIDO"


def ocr_patient_name(image_path: str) -> str:
    """Lê o nome do paciente da faixa superior da imagem.

    Contrato: retorna o nome normalizado (uppercase, sem acentos) ou
    "DESCONHECIDO". Nunca propaga exceção.
    """
    if is_macos():
        return _ocr_macos(image_path)
    return _ocr_stub(image_path)


# ------------------------------------------------------------------
# Composição do painel 2×2 — NSImage (macOS) / cópia (stub)
# ------------------------------------------------------------------
_COMPOSE_SWIFT_SRC = r"""
import AppKit
import Foundation

let args = CommandLine.arguments
let paths = Array(args[1..<(args.count - 1)])
let outputPath = args.last!
let tileSize: CGFloat = 300

let canvas = NSImage(size: NSSize(width: 600, height: 600))
canvas.lockFocus()
NSColor.black.setFill()
NSRect(x: 0, y: 0, width: 600, height: 600).fill()

let positions: [(CGFloat, CGFloat)] = [(0, 300), (300, 300), (0, 0), (300, 0)]
for (i, path) in paths.prefix(4).enumerated() {
    guard let img = NSImage(contentsOfFile: path) else { continue }
    let (x, y) = positions[i]
    let s = min(tileSize / img.size.width, tileSize / img.size.height)
    let w = img.size.width * s, h = img.size.height * s
    img.draw(in: NSRect(x: x + (tileSize - w) / 2, y: y + (tileSize - h) / 2, width: w, height: h))
}
canvas.unlockFocus()

if let tiff = canvas.tiffRepresentation,
   let bmp = NSBitmapImageRep(data: tiff),
   let png = bmp.representation(using: .png, properties: [:]) {
    try? png.write(to: URL(fileURLWithPath: outputPath))
    print("OK")
}
"""

_COMPOSE_BINARY_PATH = None


def _get_compose_binary():
    global _COMPOSE_BINARY_PATH
    if _COMPOSE_BINARY_PATH and os.path.exists(_COMPOSE_BINARY_PATH):
        return _COMPOSE_BINARY_PATH
    import subprocess

    try:
        src_file = tempfile.NamedTemporaryFile(suffix=".swift", delete=False, mode="w")
        src_file.write(_COMPOSE_SWIFT_SRC)
        src_file.close()
        bin_path = src_file.name.replace(".swift", "")
        result = subprocess.run(
            ["swiftc", src_file.name, "-o", bin_path],
            capture_output=True, timeout=60,
        )
        os.unlink(src_file.name)
        if result.returncode == 0:
            _COMPOSE_BINARY_PATH = bin_path
            return bin_path
    except Exception:
        pass
    return None


def _compose_macos(image_paths, output_path: str) -> bool:
    import subprocess

    binary = _get_compose_binary()
    if binary is None:
        return False
    try:
        result = subprocess.run(
            [binary] + list(image_paths) + [output_path],
            capture_output=True, text=True, timeout=20,
        )
        return result.stdout.strip() == "OK"
    except Exception:
        return False


def _compose_stub(image_paths, output_path: str) -> bool:
    """Stub Linux: copia a primeira imagem como placeholder do painel.

    A composição 2×2 real depende de NSImage (macOS); aqui apenas garante
    que um arquivo de output seja criado para o pipeline funcionar end-to-end.
    """
    try:
        if not image_paths:
            return False
        shutil.copy(image_paths[0], output_path)
        return True
    except Exception:
        return False


def compose_panel(image_paths, output_path: str) -> bool:
    """Gera um painel 600×600 a partir de 1–4 imagens.

    Contrato: fundo preto, cada imagem fit-dentro-do-tile sem distorção,
    grava PNG em output_path. Retorna True em sucesso, False em falha.
    """
    if is_macos():
        return _compose_macos(image_paths, output_path)
    return _compose_stub(image_paths, output_path)


# ------------------------------------------------------------------
# Notificação nativa macOS — osascript (macOS) / print (stub)
# ------------------------------------------------------------------
def notify_macos(title: str, message: str, subtitle: str = "") -> None:
    """Exibe notificação no Centro de Notificações do macOS (som "Glass").

    Silenciosa em caso de falha — nunca propaga exceção.
    """
    if not is_macos():
        print(f"[NOTIFY] {title} — {subtitle}: {message}")
        return
    import subprocess

    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" '
        f'subtitle "{esc(subtitle)}" '
        f'sound name "Glass"'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass


# ------------------------------------------------------------------
# Abrir no Finder — open -R (macOS) / print (stub)
# ------------------------------------------------------------------
def open_in_finder(path: str) -> None:
    """Abre o Finder com o arquivo selecionado. Silenciosa em falha."""
    if not is_macos():
        print(f"[FINDER] Abriria: {path}")
        return
    import subprocess

    try:
        subprocess.run(["open", "-R", path], capture_output=True, timeout=10)
    except Exception:
        pass


# ------------------------------------------------------------------
# Seletor de pasta nativo — osascript "choose folder" (macOS) / stub
# ------------------------------------------------------------------
def choose_folder():
    """Abre o diálogo nativo "choose folder" do macOS e devolve o caminho POSIX.

    Retorna None se o usuário cancelar ou em caso de falha. Em SO não-macOS
    devolve None (stub) — nunca propaga exceção.
    """
    if not is_macos():
        print("[CHOOSE] Seletor de pasta indisponível neste SO (stub).")
        return None
    import subprocess

    script = (
        'POSIX path of (choose folder '
        'with prompt "Escolha a pasta para monitorar")'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        # Cancelar => returncode != 0 e stdout vazio.
        return result.stdout.strip() or None
    except Exception:
        return None
