from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import (MessageEvent, TextMessage, TextSendMessage, ImageMessage,
                            ButtonsTemplate, MessageAction, TemplateSendMessage, FlexSendMessage)
from linebot.exceptions import InvalidSignatureError
import os
import json
from main import process_image_with_gemini
from prompts import OCR_PROMPT
from linebot.models.events import PostbackEvent
from linebot.models import PostbackAction
import requests


app = Flask(__name__)
load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

SAVE_DIR = "received_images"
os.makedirs(SAVE_DIR, exist_ok=True)

def send_to_gas(data, user_id):
    """
    GASにデータを送信し、成功時にLINEユーザーへプッシュメッセージを送信。
    """
    GAS_URL = "https://script.google.com/macros/s/AKfycbzwfrJuQNOKhEOI6nQeWDBzb8NuTIyJ3ShkZyQjg4bb_PdFTcR6cMfjH1AF_xhqLLX0/exec"
    payload = data.copy()
    payload["userId"] = user_id

    try:
        app.logger.info(f"Sending data to GAS: {payload}")
        response = requests.post(GAS_URL, json=payload)
        if response.status_code == 200:
            app.logger.info(f"GASにデータを送信成功: {response.text}")
            # 成功メッセージをプッシュメッセージとして送信
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text="データが正常に送信されました！")
            )
        else:
            app.logger.error(f"GAS送信エラー: {response.status_code} - {response.text}")
            # 失敗メッセージをプッシュメッセージとして送信
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text="エラー: データ送信に失敗しました。")
            )
    except Exception as e:
        app.logger.error(f"GAS送信中に例外発生: {e}")
        # 例外発生時のエラーメッセージをプッシュメッセージとして送信
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text="エラー: データ送信中に問題が発生しました。")
        )



        
def clean_and_parse_json(response_text):
    """
    JSONデータのクリーンアップとパース
    """
    try:
        if response_text.startswith("```json"):
            response_text = response_text.lstrip("```json\n").rstrip("\n```")
        data = json.loads(response_text)
        return data
    except json.JSONDecodeError as e:
        app.logger.error(f"JSON Decode Error: {e}")
        return None

def create_flex_message_with_buttons(response_text):
    """
    JSON形式のレスポンスを解析し、Flex Messageを作成（名前と値を横並び、余白調整）。
    """
    try:
        data = clean_and_parse_json(response_text)
        if data is None:
            raise ValueError("JSONデータの処理に失敗しました。")

        row_contents = []
        for key, value in data.items():
            display_value = str(value) if value is not None else "N/A"

            # 名前ボタンと値ボタンを横並びで配置
            row_contents.append({
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": f"{key[:10]}",
                            "data": json.dumps({"key": key, "edit_type": "name"}),
                            "displayText": f"{key}の名前を選択しました"
                        },
                        "style": "link",
                        "height": "sm",
                        "margin": "none"
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": f"{display_value[:10]}",
                            "data": json.dumps({"key": key, "edit_type": "value"}),
                            "displayText": f"{key}の値を選択しました"
                        },
                        "style": "secondary",
                        "height": "sm",
                        "margin": "none"
                    }
                ],
                
                "spacing": "none",
                "margin": "none",
                "padding":"0px",
                # 行間の余白を追加
            })

        # Flex Message本体を作成
        flex_message = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "修正する項目を選択してください",
                        "weight": "bold",
                        "size": "sm",
                        "wrap": True,
                        "margin": "md"  # タイトル部分の余白
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": row_contents,
                        "margin": "md",  # コンテンツ全体の余白
                        "paddingAll": "10px"  # 内側の余白
                    }
                ],
                "spacing": "none",
                "paddingAll": "10px"
            }
        }

        return flex_message

    except ValueError as e:
        app.logger.error(f"Flex Message生成エラー: {e}")
        app.logger.error(f"入力データ: {response_text}")
        return None


user_state = {}  # ユーザーの状態を保存

import threading

@handler.add(PostbackEvent)
def handle_postback(event):
    """
    Postbackイベントを処理する。
    """
    try:
        data = json.loads(event.postback.data)

        if data.get("action") == "get_result":
            user_id = event.source.user_id
            user_data = user_state.get(user_id, {}).get("data")

            if not user_data:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="エラー: 登録するデータがありません。")
                )
                return

            # 仮返信を即時実行
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="データ送信を開始しました。完了通知をお待ちください。")
            )

            # GASにデータを非同期送信
            threading.Thread(target=send_to_gas, args=(user_data, user_id)).start()
            return

        # その他のPostbackイベントの処理
        key = data.get("key")
        edit_type = data.get("edit_type")
        user_id = event.source.user_id
        existing_data = user_state.get(user_id, {}).get("data", {})

        # ステートを保存
        user_state[user_id] = {
            "pending_key": key,
            "edit_type": edit_type,
            "data": existing_data
        }

        prompt_text = (
            f"{key}の新しい名前を入力してください。" if edit_type == "name"
            else f"{key}の新しい金額を入力してください。"
        )

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=prompt_text)
        )

    except Exception as e:
        app.logger.error(f"Error in handle_postback: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラー: 処理中に問題が発生しました。")
        )


@app.route("/callback", methods=['POST'])
def callback():
    """
    LINE Webhookのエントリポイント。
    """
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return '正常動作OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    """
    ユーザーのテキストメッセージを処理し、名前または値を変更。
    """
    try:
        user_id = event.source.user_id
        user_text = event.message.text.strip()

        # ステートが存在しない場合にエラーメッセージを送信
        if user_id not in user_state or "data" not in user_state[user_id]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="エラー: 画像を送信してから修正操作をしてください。")
            )
            return

        pending_key = user_state[user_id].get("pending_key")
        edit_type = user_state[user_id].get("edit_type")
        existing_data = user_state[user_id].get("data", {})

        if not pending_key or not edit_type:
            raise ValueError("修正対象のキーまたは編集タイプが設定されていません。")

        if not existing_data:
            raise ValueError("修正対象のデータが初期化されていません。")

        # 名前の更新
        if edit_type == "name":
            if pending_key in existing_data:
                updated_data = {user_text: existing_data.pop(pending_key)}
                existing_data.update(updated_data)
                reply_text = f"{pending_key}の名前を '{user_text}' に更新しました。"
            else:
                raise KeyError(f"修正対象のキー '{pending_key}' が存在しません。")

        # 値の更新
        elif edit_type == "value":
            if pending_key in existing_data:
                existing_data[pending_key] = user_text
                reply_text = f"{pending_key}の値を '{user_text}' に更新しました。"
            else:
                raise KeyError(f"修正対象のキー '{pending_key}' が存在しません。")

        else:
            raise ValueError("不明な編集タイプです。")

        # Flex Messageを再生成
        updated_flex_message = create_flex_message_with_buttons(json.dumps(existing_data))
        if updated_flex_message is None:
            raise ValueError(f"Flex Messageの生成に失敗しました。データ: {existing_data}")
        text_button = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "間違えている部分を押してください",
                        "weight": "bold",
                        "size": "sm",
                    },
                ]
            }
        }
        # 「すべて正しいので診断結果をもらう」のボタンを作成
        diagnosis_button = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "登録しますか？",
                        "weight": "bold",
                        "size": "sm",
                        "margin": "md"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "金額を登録する",
                                    "data": json.dumps({"action": "get_result"})
                                },
                                "style": "primary",
                                "margin": "sm"
                            }
                        ]
                    }
                ]
            }
        }

        # メッセージをまとめて送信
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text=reply_text),
                FlexSendMessage(
                    alt_text="更新されたデータ",
                    contents=updated_flex_message
                ),
                FlexSendMessage(
                    alt_text="o",
                    contents=text_button,
                ),
                FlexSendMessage(
                    alt_text="合計金額",
                    contents=diagnosis_button
                )
            ]
        )

        # 更新後のデータを保存
        user_state[user_id]["data"] = existing_data

    except Exception as e:
        app.logger.error(f"Unexpected Error in handle_text_message: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラー: 修正処理中に問題が発生しました。")
        )



@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    """
    画像メッセージを処理し、初期のFlex Messageを表示。
    """
    try:
        user_id = event.source.user_id
        message_content = line_bot_api.get_message_content(event.message.id)
        image_path = os.path.join(SAVE_DIR, f"received_image_{event.message.id}.jpg")

        with open(image_path, 'wb') as fd:
            for chunk in message_content.iter_content():
                fd.write(chunk)

        # 画像解析を実行
        response_text = process_image_with_gemini(image_path, OCR_PROMPT)
        parsed_data = clean_and_parse_json(response_text)

        if not parsed_data:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="エラー: JSONデータの解析に失敗しました。")
            )
            return

        # 初期データを保存
        user_state[user_id] = {"data": parsed_data}
        # Flex Messageを生成して表示
        flex_message = create_flex_message_with_buttons(json.dumps(parsed_data))
        if not flex_message:
            raise ValueError("Flex Messageの生成に失敗しました。")
        text_button = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "間違えている部分を押してください",
                        "weight": "bold",
                        "size": "sm",
                    },
                ]
            }
        }

        
        # 診断結果を受け取るボタンを生成
        diagnosis_button = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "登録しますか？",
                        "weight": "bold",
                        "size": "sm",
                        "margin": "md"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "金額を登録する",
                                    "data": json.dumps({"action": "get_result"})
                                },
                                "style": "primary",
                                "margin": "sm"
                            }
                        ]
                    }
                ]
            }
        }

        # Flex Messageと診断結果ボタンを送信
        line_bot_api.reply_message(
            event.reply_token,
            [
                
                FlexSendMessage(
                    alt_text="解析結果",
                    contents=flex_message
                ),
                FlexSendMessage(
                    alt_text="o",
                    contents=text_button 
                ),
                FlexSendMessage(
                    alt_text="合計金額",
                    contents=diagnosis_button
                )
            ]
        )

    except Exception as e:
        app.logger.error(f"Error in handle_image_message: {e}")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラー: 画像処理中に問題が発生しました。")
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000,debug=True)
