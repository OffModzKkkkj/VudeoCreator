#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  VideoCreator — Criador Automático de Vídeos com IA            ║
║  Google Gemini · Edge TTS · yt-dlp · FFmpeg · Pillow           ║
╠══════════════════════════════════════════════════════════════════╣
║  Instalar:  pip install google-generativeai edge-tts Pillow     ║
║  Extras:    apt install ffmpeg yt-dlp (ou pip install yt-dlp)  ║
║  Rodar:     export GEMINI_API_KEY=sua_chave                     ║
║             python video_creator.py <URL_DO_VIDEO>              ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os, re, json, subprocess, asyncio, sys, textwrap
from pathlib import Path


# ══════════════════════════════════════════════════════════════
# CONFIGURAÇÕES
# ══════════════════════════════════════════════════════════════


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp")

NUM_VIDEOS = 3
MIN_DURATION = 180
MAX_DURATION = 600

TTS_VOICE = "pt-BR-FranciscaNeural"
TTS_VOICES = {
    "pt-BR": "pt-BR-FranciscaNeural",
    "pt-BR-male": "pt-BR-AntonioNeural",
    "en-US": "en-US-AriaNeural",
    "en-US-male": "en-US-GuyNeural",
}

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

GEMINI_MODEL = "gemini-1.5-flash"

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BITRATE = "2000k"
AUDIO_BITRATE = "192k"

# ══════════════════════════════════════════════════════════════
# DOWNLOADER (yt-dlp)
# ══════════════════════════════════════════════════════════════

# from config import  # config inline abaixo TEMP_DIR


def ensure_dirs():
    os.makedirs(TEMP_DIR, exist_ok=True)


def download_video(url: str) -> dict:
    ensure_dirs()
    info_file = os.path.join(TEMP_DIR, "info.json")

    print(f"\n📥 Baixando informações do vídeo: {url}")

    info_cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-playlist",
        url,
    ]
    result = subprocess.run(info_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Erro ao obter info do vídeo: {result.stderr}")

    info = json.loads(result.stdout)
    duration = int(info.get("duration", 0))
    title = info.get("title", "video")
    description = info.get("description", "")

    print(f"✅ Título: {title}")
    print(f"⏱️  Duração: {duration}s ({duration // 60}min {duration % 60}s)")

    video_path = os.path.join(TEMP_DIR, "source.mp4")

    if os.path.exists(video_path):
        print("⚡ Vídeo já baixado, pulando download...")
        return {
            "path": video_path,
            "title": title,
            "description": description,
            "duration": duration,
            "url": url,
        }

    print("📥 Baixando vídeo (pode demorar)...")

    dl_cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--write-subs",
        "--write-auto-subs",
        "--sub-lang", "pt,pt-BR,en",
        "--convert-subs", "srt",
        "-o", video_path,
        "--no-playlist",
        url,
    ]

    process = subprocess.Popen(
        dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in process.stdout:
        line = line.strip()
        if "[download]" in line or "Merging" in line or "Destination" in line:
            print(f"  {line}")
    process.wait()

    if process.returncode != 0:
        raise RuntimeError("Falha ao baixar o vídeo")

    subs = _load_subtitles()

    return {
        "path": video_path,
        "title": title,
        "description": description,
        "duration": duration,
        "url": url,
        "subtitles": subs,
    }


def _load_subtitles() -> str:
    for fname in os.listdir(TEMP_DIR):
        if fname.startswith("source") and fname.endswith(".srt"):
            path = os.path.join(TEMP_DIR, fname)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = []
            for line in content.split("\n"):
                line = line.strip()
                if (
                    line
                    and not line.isdigit()
                    and "-->" not in line
                    and not line.startswith("{")
                ):
                    lines.append(line)
            text = " ".join(lines)
            print(f"📝 Legendas encontradas ({len(text)} chars)")
            return text[:8000]
    return ""


def get_video_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data["format"].get("duration", 0))
    return 0.0


def cut_segment(input_path: str, start: float, end: float, output_path: str):
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        cmd2 = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-preset", "ultrafast",
            output_path,
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        if result2.returncode != 0:
            raise RuntimeError(f"Erro ao cortar segmento: {result2.stderr}")

# ══════════════════════════════════════════════════════════════
# AI TOOLS (Gemini)
# ══════════════════════════════════════════════════════════════

import google.generativeai as genai
# from config import  # config inline abaixo GEMINI_API_KEY, GEMINI_MODEL, NUM_VIDEOS, MIN_DURATION, MAX_DURATION


def init_gemini():
    if not GEMINI_API_KEY:
        raise ValueError(
            "❌ GEMINI_API_KEY não definida!\n"
            "   Obtenha grátis em: https://aistudio.google.com/app/apikey\n"
            "   Depois: export GEMINI_API_KEY='sua_chave'"
        )
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(GEMINI_MODEL)


def generate_video_plans(video_info: dict) -> list[dict]:
    model = init_gemini()

    title = video_info.get("title", "")
    description = video_info.get("description", "")[:500]
    subtitles = video_info.get("subtitles", "")[:4000]
    duration = video_info.get("duration", 0)

    context = f"""
Título original: {title}
Descrição: {description}
Duração total: {duration}s ({duration // 60} minutos)
Transcrição parcial: {subtitles if subtitles else "Não disponível"}
"""

    prompt = f"""
Você é um especialista em criar vídeos virais para o YouTube.

Analise este vídeo e crie {NUM_VIDEOS} planos de vídeos derivados, cada um de {MIN_DURATION // 60} a {MAX_DURATION // 60} minutos.

{context}

Para cada vídeo, retorne um JSON com:
- "title": título otimizado para SEO e cliques (máx 70 chars, português)
- "description": descrição do YouTube com palavras-chave (máx 300 chars)
- "hook": frase de abertura impactante para o vídeo (máx 2 frases)
- "narration": narração completa de 3-5 minutos (600-1000 palavras) em português do Brasil, natural e envolvente
- "start_percent": ponto de início no vídeo original (0-70, número inteiro)
- "end_percent": ponto de fim no vídeo original (start_percent+20 a start_percent+60)
- "thumbnail_text": texto principal da thumbnail (máx 4 palavras, MAIÚSCULAS, impactante)
- "tags": lista de 10 tags relevantes para YouTube
- "thumbnail_bg_color": cor de fundo hex para thumbnail (ex: "#FF4500")
- "thumbnail_text_color": cor do texto hex (ex: "#FFFFFF")

IMPORTANTE:
- Cada vídeo deve ter um ângulo ÚNICO e diferente dos outros
- Títulos devem gerar curiosidade e cliques
- A narração deve ser fluida, como um locutor profissional
- Varie os segmentos para cobrir partes diferentes do vídeo original
- Use português brasileiro natural

Retorne APENAS o JSON válido, sem markdown, sem comentários:
{{"videos": [...]}}
"""

    print("\n🤖 Gerando planos de vídeo com Gemini AI...")
    response = model.generate_content(prompt)
    raw = response.text.strip()

    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)

    data = json.loads(raw)
    plans = data.get("videos", data) if isinstance(data, dict) else data

    if isinstance(plans, list):
        print(f"✅ {len(plans)} planos de vídeo gerados!")
        for i, p in enumerate(plans, 1):
            print(f"   {i}. {p.get('title', 'Sem título')}")
        return plans

    raise ValueError("Resposta inválida da API Gemini")


def generate_youtube_metadata(plan: dict) -> dict:
    model = init_gemini()

    prompt = f"""
Crie metadados otimizados para YouTube para este vídeo:
Título: {plan.get('title')}
Descrição base: {plan.get('description')}
Tags: {', '.join(plan.get('tags', []))}

Retorne JSON com:
- "title": título final (máx 70 chars)
- "description": descrição completa com emojis, hashtags e call-to-action (máx 500 chars)
- "tags": lista de 15 tags otimizadas

Retorne APENAS o JSON, sem markdown.
"""
    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    return json.loads(raw)

# ══════════════════════════════════════════════════════════════
# TTS (Edge TTS)
# ══════════════════════════════════════════════════════════════

import edge_tts
# from config import  # config inline abaixo TTS_VOICE, TTS_VOICES, TEMP_DIR


async def _generate_tts(text: str, output_path: str, voice: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def generate_voice(text: str, output_path: str, voice_key: str = "pt-BR") -> str:
    voice = TTS_VOICES.get(voice_key, TTS_VOICE)
    print(f"🎙️  Gerando voz AI ({voice})...")

    text = text.replace("*", "").replace("#", "").replace("_", "")
    if len(text) > 5000:
        text = text[:5000] + "..."

    asyncio.run(_generate_tts(text, output_path, voice))

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size_kb = os.path.getsize(output_path) // 1024
        print(f"✅ Áudio gerado: {os.path.basename(output_path)} ({size_kb}KB)")
        return output_path
    raise RuntimeError("Falha ao gerar áudio TTS")


def list_available_voices() -> list[str]:
    loop = asyncio.get_event_loop()

    async def _list():
        voices = await edge_tts.list_voices()
        return [v["ShortName"] for v in voices if "pt" in v["Locale"].lower() or "en" in v["Locale"].lower()]

    return loop.run_until_complete(_list())

# ══════════════════════════════════════════════════════════════
# THUMBNAIL (Pillow)
# ══════════════════════════════════════════════════════════════

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
# from config import  # config inline abaixo THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, TEMP_DIR


def _get_font(size: int):
    font_paths = [
        "/system/fonts/Roboto-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def extract_frame(video_path: str, time_sec: float, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and os.path.exists(output_path)


def create_thumbnail(
    video_path: str,
    frame_time: float,
    text: str,
    bg_color: str,
    text_color: str,
    output_path: str,
    index: int = 1,
) -> str:
    print(f"🖼️  Criando thumbnail: '{text}'")

    W, H = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT
    img = Image.new("RGB", (W, H), bg_color)

    frame_path = os.path.join(TEMP_DIR, f"frame_{index}.jpg")
    if extract_frame(video_path, frame_time, frame_path):
        try:
            frame = Image.open(frame_path).convert("RGB")
            frame = frame.resize((W, H), Image.LANCZOS)
            frame = ImageEnhance.Brightness(frame).enhance(0.45)
            frame = frame.filter(ImageFilter.GaussianBlur(radius=2))
            img.paste(frame, (0, 0))
        except Exception:
            pass

    draw = ImageDraw.Draw(img)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    ov_draw.rectangle([0, H // 2, W, H], fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    accent_color = bg_color
    bar_h = 12
    draw.rectangle([0, H - bar_h, W, H], fill=accent_color)

    lines = textwrap.wrap(text.upper(), width=18)
    font_size = 100 if len(lines) == 1 else 80 if len(lines) == 2 else 65
    font = _get_font(font_size)

    total_height = len(lines) * (font_size + 10)
    y = (H - total_height) // 2 + H // 8

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (W - text_w) // 2

        for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (0, -4), (0, 4), (-4, 0), (4, 0)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0))

        draw.text((x, y), line, font=font, fill=text_color)
        y += font_size + 10

    number_font = _get_font(36)
    badge_text = f"PARTE {index}"
    draw.text((30, 30), badge_text, font=number_font, fill=text_color)

    img.save(output_path, "JPEG", quality=95)
    size_kb = os.path.getsize(output_path) // 1024
    print(f"✅ Thumbnail criada: {os.path.basename(output_path)} ({size_kb}KB)")
    return output_path

# ══════════════════════════════════════════════════════════════
# VIDEO EDITOR (FFmpeg)
# ══════════════════════════════════════════════════════════════

# from config import  # config inline abaixo TEMP_DIR, OUTPUT_DIR, MIN_DURATION, MAX_DURATION
# from downloader import  # inline cut_segment, get_video_duration


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)


def mix_audio_with_voice(
    video_path: str,
    voice_path: str,
    output_path: str,
    voice_volume: float = 1.0,
    original_volume: float = 0.08,
):
    print("🎬 Mixando vídeo com narração AI...")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", voice_path,
        "-filter_complex",
        f"[0:a]volume={original_volume}[orig];"
        f"[1:a]volume={voice_volume}[voice];"
        f"[orig][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        cmd2 = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-filter_complex",
            f"[0:a]volume={original_volume}[orig];"
            f"[1:a]volume={voice_volume}[voice];"
            f"[orig][voice]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)
        if result2.returncode != 0:
            raise RuntimeError(f"Erro ao mixar áudio: {result2.stderr[-500:]}")


def add_intro_card(
    video_path: str,
    title: str,
    output_path: str,
    bg_color: str = "#1a1a2e",
):
    duration = 3
    safe_title = title.replace("'", "\\'").replace('"', '\\"')[:60]

    intro_path = os.path.join(TEMP_DIR, "intro_card.mp4")

    cmd_intro = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={bg_color.replace('#', '0x')}:size=1280x720:duration={duration}:rate=30",
        "-vf", f"drawtext=text='{safe_title}':fontcolor=white:fontsize=48:"
               f"x=(w-text_w)/2:y=(h-text_h)/2:shadowcolor=black:shadowx=3:shadowy=3",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-an",
        intro_path,
    ]
    result = subprocess.run(cmd_intro, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(intro_path):
        print("⚠️  Intro card ignorado (ffmpeg drawtext não disponível)")
        os.rename(video_path, output_path) if video_path != output_path else None
        return

    concat_list = os.path.join(TEMP_DIR, "concat.txt")
    with open(concat_list, "w") as f:
        f.write(f"file '{intro_path}'\n")
        f.write(f"file '{video_path}'\n")

    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        output_path,
    ]
    result2 = subprocess.run(cmd_concat, capture_output=True, text=True)
    if result2.returncode != 0:
        import shutil
        shutil.copy2(video_path, output_path)


def build_final_video(
    source_video: str,
    voice_audio: str,
    plan: dict,
    index: int,
    total_duration: float,
) -> str:
    ensure_dirs()

    start_pct = float(plan.get("start_percent", (index - 1) * 25))
    end_pct = float(plan.get("end_percent", start_pct + 35))

    start_pct = max(0, min(start_pct, 70))
    end_pct = max(start_pct + 20, min(end_pct, 95))

    start_sec = (start_pct / 100) * total_duration
    end_sec = (end_pct / 100) * total_duration

    seg_duration = end_sec - start_sec
    if seg_duration < MIN_DURATION:
        end_sec = start_sec + MIN_DURATION
    if seg_duration > MAX_DURATION:
        end_sec = start_sec + MAX_DURATION

    end_sec = min(end_sec, total_duration)

    print(f"\n✂️  Cortando segmento: {int(start_sec)}s → {int(end_sec)}s ({int(end_sec - start_sec)}s)")

    segment_path = os.path.join(TEMP_DIR, f"segment_{index}.mp4")
    cut_segment(source_video, start_sec, end_sec, segment_path)

    mixed_path = os.path.join(TEMP_DIR, f"mixed_{index}.mp4")
    mix_audio_with_voice(segment_path, voice_audio, mixed_path)

    safe_title = "".join(c for c in plan.get("title", f"video_{index}") if c.isalnum() or c in " -_")
    safe_title = safe_title[:50].strip().replace(" ", "_")

    final_path = os.path.join(OUTPUT_DIR, f"video_{index}_{safe_title}.mp4")
    add_intro_card(mixed_path, plan.get("title", ""), final_path)

    final_duration = get_video_duration(final_path)
    size_mb = os.path.getsize(final_path) // (1024 * 1024)
    print(f"✅ Vídeo {index} finalizado: {os.path.basename(final_path)}")
    print(f"   📁 Tamanho: {size_mb}MB | ⏱️  Duração: {int(final_duration)}s")

    return final_path

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║          VIDEO CREATOR - YouTube Automation          ║
║  Gera 3 vídeos com IA, voz, thumbnail e título       ║
║  APIs gratuitas: Gemini + edge-tts + yt-dlp          ║
╚══════════════════════════════════════════════════════╝
"""
import shutil
import argparse
from colorama import init, Fore, Style

init(autoreset=True)


def banner():
    print(Fore.CYAN + Style.BRIGHT + """
╔══════════════════════════════════════════════════════╗
║          🎬 VIDEO CREATOR - YouTube Automation        ║
║     Gemini AI + Edge TTS + yt-dlp + ffmpeg            ║
╚══════════════════════════════════════════════════════╝
""")


def check_dependencies():
    missing = []
    tools = {"ffmpeg": "ffmpeg -version", "ffprobe": "ffprobe -version", "yt-dlp": "yt-dlp --version"}
        for tool, cmd in tools.items():
        r = subprocess.run(cmd.split(), capture_output=True)
        if r.returncode != 0:
            missing.append(tool)
    if missing:
        print(Fore.RED + f"❌ Ferramentas ausentes: {', '.join(missing)}")
        print(Fore.YELLOW + "\nInstale no Termux:")
        print("  pkg install ffmpeg yt-dlp")
        sys.exit(1)


def check_api_key():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print(Fore.RED + "❌ GEMINI_API_KEY não configurada!")
        print(Fore.YELLOW + """
Como obter (GRÁTIS):
  1. Acesse: https://aistudio.google.com/app/apikey
  2. Clique em "Create API Key"
  3. Copie a chave

Configure no Termux:
  export GEMINI_API_KEY='sua_chave_aqui'
  
Ou adicione ao ~/.bashrc para sempre funcionar:
  echo "export GEMINI_API_KEY='sua_chave'" >> ~/.bashrc
""")
        sys.exit(1)


def save_metadata(plan: dict, video_path: str, thumbnail_path: str, index: int):
    # from config import  # config inline abaixo OUTPUT_DIR
    meta_path = os.path.join(OUTPUT_DIR, f"video_{index}_metadata.json")
    meta = {
        "titulo": plan.get("title", ""),
        "descricao": plan.get("description", ""),
        "tags": plan.get("tags", []),
        "thumbnail": thumbnail_path,
        "video": video_path,
        "dicas_youtube": {
            "categoria": "Entretenimento",
            "idioma": "pt-BR",
            "visibilidade": "Público",
            "nao_adequado_criancas": False,
            "permitir_comentarios": True,
        }
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(Fore.GREEN + f"📋 Metadados salvos: {os.path.basename(meta_path)}")
    return meta_path


def print_upload_guide(plans: list, output_files: list):
    print(Fore.CYAN + Style.BRIGHT + "\n" + "="*56)
    print(Fore.CYAN + Style.BRIGHT + "  📤 GUIA DE UPLOAD - COPIE E COLE NO YOUTUBE")
    print(Fore.CYAN + Style.BRIGHT + "="*56)

    for i, (plan, files) in enumerate(zip(plans, output_files), 1):
        print(Fore.YELLOW + Style.BRIGHT + f"\n  VÍDEO {i}:")
        print(Fore.WHITE + f"  Arquivo:    {files.get('video', 'N/A')}")
        print(Fore.WHITE + f"  Thumbnail:  {files.get('thumbnail', 'N/A')}")
        print(Fore.GREEN + f"  Título:     {plan.get('title', '')}")
        print(Fore.WHITE + f"  Descrição:  {plan.get('description', '')[:100]}...")
        tags = plan.get('tags', [])
        print(Fore.WHITE + f"  Tags:       {', '.join(tags[:8])}")

    print(Fore.CYAN + Style.BRIGHT + "\n" + "="*56)
    print(Fore.GREEN + "  ✅ Passos para postar:")
    print(Fore.WHITE + "  1. Abra studio.youtube.com")
    print(Fore.WHITE + "  2. Clique em 'Criar' → 'Enviar vídeos'")
    print(Fore.WHITE + "  3. Selecione o arquivo .mp4")
    print(Fore.WHITE + "  4. Cole o título do metadata.json")
    print(Fore.WHITE + "  5. Cole a descrição e tags")
    print(Fore.WHITE + "  6. Faça upload da thumbnail .jpg")
    print(Fore.WHITE + "  7. Publique!")
    print(Fore.CYAN + Style.BRIGHT + "="*56 + "\n")


def clean_temp():
    # from config import  # config inline abaixo TEMP_DIR
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
        print(Fore.YELLOW + "🧹 Pasta temporária limpa")


def main():
    banner()

    parser = argparse.ArgumentParser(
        description="Gera vídeos para YouTube a partir de uma URL"
    )
    parser.add_argument("url", nargs="?", help="URL do vídeo (YouTube, etc.)")
    parser.add_argument("--voice", default="pt-BR",
                        choices=["pt-BR", "pt-BR-male", "en-US", "en-US-male"],
                        help="Voz para narração (padrão: pt-BR feminina)")
    parser.add_argument("--no-clean", action="store_true",
                        help="Não limpar arquivos temporários")
    parser.add_argument("--skip-download", action="store_true",
                        help="Pular download (usar vídeo já baixado em temp/source.mp4)")
    parser.add_argument("--list-voices", action="store_true",
                        help="Listar vozes disponíveis")
    args = parser.parse_args()

    if args.list_voices:
        print(Fore.CYAN + "\nVozes disponíveis:")
        # from tts import  # inline list_available_voices
        for v in list_available_voices():
            print(f"  - {v}")
        return

    if not args.url and not args.skip_download:
        print(Fore.YELLOW + "Uso: python main.py <URL_DO_VIDEO>")
        print(Fore.YELLOW + "Exemplo: python main.py 'https://youtube.com/watch?v=...'")
        sys.exit(1)

    check_dependencies()
    check_api_key()

    # from config import  # config inline abaixo OUTPUT_DIR, TEMP_DIR
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    # from downloader import  # inline download_video
    # from ai_tools import  # inline generate_video_plans
    # from tts import  # inline generate_voice
    # from thumbnail import  # inline create_thumbnail
    # from video_editor import  # inline build_final_video
    # from downloader import  # inline get_video_duration

    if args.skip_download:
        source_path = os.path.join(TEMP_DIR, "source.mp4")
        if not os.path.exists(source_path):
            print(Fore.RED + f"❌ Vídeo não encontrado em: {source_path}")
            sys.exit(1)
        video_info = {
            "path": source_path,
            "title": "Vídeo Local",
            "description": "",
            "duration": int(get_video_duration(source_path)),
            "subtitles": "",
        }
        print(Fore.GREEN + f"✅ Usando vídeo local: {source_path}")
    else:
        video_info = download_video(args.url)

    source_path = video_info["path"]
    total_duration = float(video_info["duration"])

    if total_duration < 180:
        print(Fore.YELLOW + f"⚠️  Vídeo muito curto ({int(total_duration)}s). Mínimo recomendado: 3 min.")

    plans = generate_video_plans(video_info)

    output_files = []
    all_plans = plans[:3]

    for i, plan in enumerate(all_plans, 1):
        print(Fore.CYAN + Style.BRIGHT + f"\n{'='*50}")
        print(Fore.CYAN + Style.BRIGHT + f"  🎬 PROCESSANDO VÍDEO {i}/3: {plan.get('title', '')[:40]}")
        print(Fore.CYAN + Style.BRIGHT + f"{'='*50}")

        narration = plan.get("narration", plan.get("hook", "Bem-vindos ao canal!"))

        voice_path = os.path.join(TEMP_DIR, f"voice_{i}.mp3")
        generate_voice(narration, voice_path, args.voice)

        start_pct = float(plan.get("start_percent", (i - 1) * 25))
        thumb_time = (start_pct / 100) * total_duration + 5

        thumb_path = os.path.join(OUTPUT_DIR, f"video_{i}_thumbnail.jpg")
        create_thumbnail(
            video_path=source_path,
            frame_time=thumb_time,
            text=plan.get("thumbnail_text", plan.get("title", "")[:20]),
            bg_color=plan.get("thumbnail_bg_color", "#1a1a2e"),
            text_color=plan.get("thumbnail_text_color", "#ffffff"),
            output_path=thumb_path,
            index=i,
        )

        final_video = build_final_video(
            source_video=source_path,
            voice_audio=voice_path,
            plan=plan,
            index=i,
            total_duration=total_duration,
        )

        meta_path = save_metadata(plan, final_video, thumb_path, i)

        output_files.append({
            "video": final_video,
            "thumbnail": thumb_path,
            "metadata": meta_path,
        })

        print(Fore.GREEN + Style.BRIGHT + f"  ✅ Vídeo {i} completo!")

    print_upload_guide(all_plans, output_files)

    if not args.no_clean:
        clean_temp()

    print(Fore.GREEN + Style.BRIGHT + f"""
🎉 PRONTO! {len(output_files)} vídeos criados em: output/
   
   Use os arquivos metadata.json para copiar título,
   descrição e tags na hora do upload no YouTube!
""")


if __name__ == "__main__":
    main()
