#!/usr/bin/env python3
# unificar_catalogos.py
# Lê dailymotion_videos.js e youtube_videos.js, converte/normaliza e gera catalogo_videos.js
# Robust: preserva '//' dentro de strings ao remover comentários

import re
import json
import os
import sys
import argparse
from datetime import datetime
import traceback

def find_matching_bracket(s, start_index):
    opening = s[start_index]
    if opening not in '[{':
        raise ValueError("start_index must point to '[' or '{'")
    stack = [opening]
    i = start_index + 1
    in_string = False
    string_char = None
    escape = False
    while i < len(s):
        ch = s[i]
        if escape:
            escape = False
        elif ch == '\\':
            escape = True
        elif in_string:
            if ch == string_char:
                in_string = False
                string_char = None
        elif ch == '"' or ch == "'":
            in_string = True
            string_char = ch
        elif ch == '[' or ch == '{':
            stack.append(ch)
        elif ch == ']' or ch == '}':
            if not stack:
                raise ValueError("Unbalanced brackets")
            top = stack.pop()
            if (top == '[' and ch != ']') or (top == '{' and ch != '}'):
                raise ValueError("Mismatched brackets")
            if not stack:
                return i
        i += 1
    raise ValueError("No matching bracket found")

def remove_js_comments(source):
    """
    Remove comentários JS (// ... e /* ... */) mas preserva // que estão dentro de strings.
    Implementação por varredura estado-ful: acompanha strings e escapes.
    """
    result_chars = []
    i = 0
    n = len(source)
    in_string = False
    string_char = None
    escape = False
    while i < n:
        ch = source[i]
        if escape:
            result_chars.append(ch)
            escape = False
            i += 1
            continue
        if in_string:
            if ch == '\\':
                result_chars.append(ch)
                escape = True
                i += 1
                continue
            if ch == string_char:
                result_chars.append(ch)
                in_string = False
                string_char = None
                i += 1
                continue
            result_chars.append(ch)
            i += 1
            continue
        else:
            # não está em string
            if ch == '"' or ch == "'":
                in_string = True
                string_char = ch
                result_chars.append(ch)
                i += 1
                continue
            # detectar comentário de linha //
            if ch == '/' and i+1 < n and source[i+1] == '/':
                i += 2
                # pular até quebra de linha (preservando a quebra)
                while i < n and source[i] not in '\r\n':
                    i += 1
                continue
            # detectar comentário de bloco /* ... */
            if ch == '/' and i+1 < n and source[i+1] == '*':
                i += 2
                while i+1 < n and not (source[i] == '*' and source[i+1] == '/'):
                    i += 1
                i += 2 if i+1 < n else 0
                continue
            # caractere normal
            result_chars.append(ch)
            i += 1
    return ''.join(result_chars)

def clean_js_array_to_json_array(array_str):
    # remover comentários (respeitando strings)
    no_comments = remove_js_comments(array_str)
    # remover vírgulas finais antes de ] ou }
    no_trailing = re.sub(r',\s*(\]|\})', r'\1', no_comments)
    return no_trailing

def parse_js_array(array_str):
    cleaned = clean_js_array_to_json_array(array_str)
    try:
        return json.loads(cleaned)
    except Exception as e:
        # Tentativas de fallback:
        # 1) converter aspas simples em duplas (valores simples)
        alt = re.sub(r"(?<=[:\s])'([^']*)'(?=[,\]\}])", r'"\1"', cleaned)
        try:
            return json.loads(alt)
        except Exception:
            # 2) colocar aspas em chaves não citadas (heurística simples)
            def quote_keys(s):
                def repl(match):
                    return '"' + match.group(1) + '":'
                return re.sub(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', repl, s)
            try:
                alt2 = quote_keys(alt)
                return json.loads(alt2)
            except Exception as e2:
                raise RuntimeError(f"Failed to parse JSON array: {e2}") from e2

def extract_array_by_key(js_text, key):
    """
    Extrai a string do array [...] associado a key: [
    Retorna string incluindo colchetes.
    """
    patterns = [rf'{re.escape(key)}\s*:\s*\[', rf'["\']{re.escape(key)}["\']\s*:\s*\[']
    for pat in patterns:
        m = re.search(pat, js_text)
        if m:
            start_bracket = js_text.find('[', m.end()-1)
            if start_bracket == -1:
                continue
            try:
                end_bracket = find_matching_bracket(js_text, start_bracket)
                return js_text[start_bracket:end_bracket+1]
            except Exception:
                continue
    return None

def load_dailymotion_items(path):
    text = open(path, 'r', encoding='utf-8').read()
    # tentar primeira abordagem: maydayEpisodes
    arr = extract_array_by_key(text, 'maydayEpisodes')
    if not arr:
        # procurar categories {... maydayEpisodes: [...] }
        m = re.search(r'categories\s*:\s*\{', text)
        if m:
            start = text.find('{', m.end()-1)
            try:
                end = find_matching_bracket(text, start)
                block = text[start:end+1]
                arr = extract_array_by_key(block, 'maydayEpisodes')
            except Exception:
                arr = None
    if not arr:
        # fallback: pegar o primeiro grande array de objetos
        m = re.search(r'\[\s*\{\s*"', text)
        if m:
            start = m.start()
            end = find_matching_bracket(text, start)
            arr = text[start:end+1]
    if not arr:
        raise RuntimeError("Não consegui localizar o array de vídeos no dailymotion_videos.js")
    items = parse_js_array(arr)
    return items

def load_youtube_items(path):
    text = open(path, 'r', encoding='utf-8').read()
    # buscar vídeos dentro do objeto youtubeMaydayVideos
    arr = extract_array_by_key(text, 'videos')
    if not arr:
        # buscar o bloco do const youtubeMaydayVideos = { ... } e extrair 'videos' dentro dele
        m = re.search(r'const\s+youtubeMaydayVideos\s*=\s*\{', text)
        if m:
            start = text.find('{', m.end()-1)
            try:
                end = find_matching_bracket(text, start)
                block = text[start:end+1]
                arr = extract_array_by_key(block, 'videos')
            except Exception:
                arr = None
    if not arr:
        raise RuntimeError("Não consegui localizar o array 'videos' no youtube_videos.js")
    items = parse_js_array(arr)
    return items

def convert_youtube_to_dailymotion_format(yt_item):
    url = yt_item.get('url') or (f"https://www.youtube.com/watch?v={yt_item.get('id')}" if yt_item.get('id') else None)
    return {
        "url": url,
        "text": yt_item.get("title") or yt_item.get("text") or "",
        "title": yt_item.get("title") or yt_item.get("text") or "",
        "videoId": yt_item.get("id") or "",
        "imageUrl": yt_item.get("thumbnail") or yt_item.get("imageUrl") or "",
        "duration": yt_item.get("duration") or "N/A",
        "views": yt_item.get("views") or "N/A",
        "is_external": False,
        "season": yt_item.get("season"),
        "episode": yt_item.get("episode"),
        "hasLocalImage": False
    }

def normalize_dailymotion_item(dm_item):
    return {
        "url": dm_item.get("url"),
        "text": dm_item.get("text") or dm_item.get("title") or "",
        "title": dm_item.get("title") or dm_item.get("text") or "",
        "videoId": dm_item.get("videoId") or dm_item.get("video_id") or "",
        "imageUrl": dm_item.get("imageUrl") or dm_item.get("image") or dm_item.get("thumbnail") or "",
        "duration": dm_item.get("duration") or "N/A",
        "views": dm_item.get("views") or "N/A",
        "is_external": dm_item.get("is_external", False),
        "season": dm_item.get("season"),
        "episode": dm_item.get("episode"),
        "hasLocalImage": dm_item.get("hasLocalImage", False)
    }

def generate_catalog(dm_items, yt_items, out_path="catalogo_videos.js"):
    combined = []
    for dm in dm_items:
        combined.append(normalize_dailymotion_item(dm))
    for yt in yt_items:
        combined.append(convert_youtube_to_dailymotion_format(yt))
    total = len(combined)
    catalog = {
        "metadata": {
            "generated": datetime.now().isoformat(),
            "source": "dailymotion + youtube",
            "totalVideos": total
        },
        "statistics": {"total_videos": total},
        "categories": {"maydayEpisodes": combined}
    }
    js_text = (
        f"// Arquivo gerado automaticamente em {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"// Catalogo unificado (Dailymotion + YouTube)\n\n"
        f"const catalogoVideos = {json.dumps(catalog, indent=2, ensure_ascii=False)};\n\n"
        "if (typeof module !== 'undefined' && module.exports) {\n"
        "  module.exports = catalogoVideos;\n"
        "} else if (typeof window !== 'undefined') {\n"
        "  window.catalogoVideos = catalogoVideos;\n"
        "}\n"
    )
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(js_text)
    return out_path, total

def main():
    parser = argparse.ArgumentParser(description="Unifica dailymotion_videos.js + youtube_videos.js em catalogo_videos.js")
    parser.add_argument('-d', '--dailymotion', default='dailymotion_videos.js', help='Caminho para dailymotion_videos.js')
    parser.add_argument('-y', '--youtube', default='youtube_videos.js', help='Caminho para youtube_videos.js')
    parser.add_argument('-o', '--output', default='catalogo_videos.js', help='Caminho do output (catalogo_videos.js)')
    args = parser.parse_args()

    try:
        if not os.path.exists(args.dailymotion) or not os.path.exists(args.youtube):
            print("Erro: verifique se os arquivos existem no diretório ou passe caminhos com -d e -y")
            sys.exit(1)

        print("🔎 Carregando Dailymotion...")
        dm_items = load_dailymotion_items(args.dailymotion)
        print(f"  ➜ Encontrados {len(dm_items)} itens no Dailymotion.")

        print("🔎 Carregando YouTube...")
        yt_items = load_youtube_items(args.youtube)
        print(f"  ➜ Encontrados {len(yt_items)} itens no YouTube.")

        print("🔁 Gerando catálogo unificado...")
        out_path, total = generate_catalog(dm_items, yt_items, out_path=args.output)
        print(f"✅ Gerado {out_path} com {total} vídeos.")

    except Exception:
        print("❌ Ocorreu um erro:")
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
