# main.py
import google.generativeai as genai
from pathlib import Path

genai.configure(api_key="AIzaSyAXhAG0134WDwXvmcUcO5_34r8cyVuxzKI")


def process_image_with_gemini(image_path: str, prompt: str) -> str:
    try:
        picture = [{
            'mime_type': 'image/jpeg',
            'data': Path(image_path).read_bytes()
        }]
        response = genai.GenerativeModel('gemini-1.5-pro').generate_content(
            contents=[prompt, picture[0]]
        )
        print("APIレスポンス:", response)  # レスポンス内容を出力
        return response.text
    except Exception as e:
        print("エラー:", e)  # エラーメッセージを出力
        return f"エラーが発生しました: {str(e)}"
