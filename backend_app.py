import os
import json
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Configuration ---
# ใส่ ID ของ Google Sheets ของคุณ
# ตรวจสอบให้แน่ใจว่าได้แชร์ Google Sheet กับ Service Account Email แล้ว
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '1VrhyathlW5pu7qJE_5mK9XWUyiaRcoM1zPFgeDd7pJo')

# ชื่อชีทข้อมูลสต็อกสินค้า
STOCK_SHEET_NAME = "ชื่อสินค้า"
# ชื่อชีทหน้า transaction ขาย
TRANSACTION_SHEET_NAME = "Transaction ขาย"

# ไฟล์ credentials.json ของ Service Account
# ใน Production ควรอ่านจาก Environment Variable หรือจาก Path ที่ปลอดภัย
GOOGLE_CREDENTIALS_FILE = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

app = Flask(__name__, static_folder='frontend', static_url_path='/')
CORS(app) # เปิดใช้งาน CORS เพื่อให้ Frontend สามารถเรียก API ได้

# --- Google Sheets Setup ---
def get_sheets_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

        # --- DEBUGGING CODE START ---
        credentials_file_path = GOOGLE_CREDENTIALS_FILE # This is typically 'credentials.json'

        if not os.path.exists(credentials_file_path):
            app.logger.error(f"Credentials file NOT found at: {os.path.abspath(credentials_file_path)}")
            raise FileNotFoundError(f"Missing credentials file: {credentials_file_path}")

        with open(credentials_file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
            if not file_content.strip():
                app.logger.error(f"Credentials file '{credentials_file_path}' is empty or contains only whitespace.")
                raise ValueError("Credentials file is empty.")

            try:
                # Attempt to parse the JSON content directly
                parsed_json = json.loads(file_content)
                app.logger.info("Credentials file content parsed successfully (JSON format is OK).")
                # You can even print a part of it for sanity check, e.g.,
                # app.logger.info(f"Client Email: {parsed_json.get('client_email', 'N/A')}")
            except json.JSONDecodeError as e:
                app.logger.error(f"Credentials file '{credentials_file_path}' is NOT valid JSON. Error: {e}")
                app.logger.error(f"Content: '{file_content[:50]}...' (first 50 chars)") # Show first 50 chars
                raise ValueError(f"Credentials file is not valid JSON: {e}")
            except Exception as e:
                app.logger.error(f"Unexpected error when reading/parsing credentials file: {e}")
                raise
        # --- DEBUGGING CODE END ---

        # Original code to use gspread
        creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file_path, scope)
        client = gspread.authorize(creds)
        return client
    except ConnectionError as e:
        # Re-raise connection errors for specific handling if needed
        raise
    except Exception as e:
        app.logger.error(f"Critical error in get_sheets_client: {e}")
        # Re-raise the original exception for the full traceback
        raise

@app.route('/')
def serve_index():
    return send_from_directory('frontend', 'index.html')

@app.route('/<path:path>')
def serve_frontend_files(path):
    # ปรับปรุง: ตรวจสอบเส้นทาง static เพื่อให้มั่นใจว่าครอบคลุม favicon.ico และไฟล์อื่นๆ
    # หากมีการใช้ static_folder='frontend' และ static_url_path='/'
    # ไฟล์เช่น /static/style.css จะถูกจับโดย /static/style.css ใน route นี้
    # หากวาง favicon.ico ที่ /frontend/favicon.ico มันจะถูกจับโดย /favicon.ico
    # ตรวจสอบว่าไฟล์ที่ร้องขออยู่ในโฟลเดอร์ 'frontend' หรือไม่
    # นี่เป็น fallback สำหรับไฟล์ที่ไม่ได้ถูกจัดการโดย static_url_path โดยตรง
    full_path = os.path.join(app.root_path, 'frontend', path)
    if os.path.exists(full_path):
        return send_from_directory('frontend', path)
    # หากยังหาไม่เจอ อาจเป็นไฟล์ static ที่เรียกผ่าน /static/path_to_file
    elif os.path.exists(os.path.join(app.root_path, 'frontend', 'static', path)):
        return send_from_directory(os.path.join(app.root_path, 'frontend', 'static'), path)
    return "File not found", 404


@app.route('/api/stock/all', methods=['GET'])
def api_get_all_stock():
    try:
        gc = get_sheets_client()
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 500 # ส่ง Error จาก get_sheets_client() ไปยัง Frontend

    try:
        sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(STOCK_SHEET_NAME)
        data = sheet.get_all_values()

        if len(data) <= 1: # ถ้ามีแค่ Header หรือไม่มีข้อมูลเลย
            return jsonify({"message": "No stock data found", "data": []}), 200

        products = []
        # ปรับปรุง: ข้ามแถวที่ 0 (Header) และวนลูปจากแถวข้อมูลจริง
        # ใช้ enumerate ร่วมกับ start=1 เพื่อให้ได้ index ของแถวใน `data` array
        for row_idx, row in enumerate(data):
            if row_idx == 0: # ข้าม Header row
                continue
            
            # ปรับปรุง: เพิ่มการตรวจสอบความยาวของแถว เพื่อป้องกัน IndexError
            if len(row) < 2: # อย่างน้อยต้องมี ชื่อสินค้า และ จำนวน
                app.logger.warning(f"Skipping malformed row (not enough columns) in stock sheet at row {row_idx + 1}: {row}")
                continue

            try:
                product_name = row[0].strip() # ตัดช่องว่างหน้าหลัง
                # ปรับปรุง: จัดการค่าว่างหรือค่าที่ไม่ใช่ตัวเลขใน quantity
                stock_quantity_str = row[1].strip()
                stock_quantity = 0 # ค่าเริ่มต้นเป็น 0 ถ้าแปลงไม่ได้
                if stock_quantity_str:
                    stock_quantity = int(stock_quantity_str)
                
                image_url = row[3].strip() if len(row) > 3 and row[3] else None
                # ตรวจสอบ URL อีกครั้ง และไม่เก็บค่าที่ไม่มีประโยชน์
                if image_url and not image_url.startswith('https://'):
                    image_url = None # ไม่ใช้ URL ที่ไม่ถูกต้อง

                products.append({
                    "name": product_name,
                    "quantity": stock_quantity,
                    "imageUrl": image_url
                })
            except ValueError as e:
                app.logger.warning(f"Skipping row due to data conversion error in stock sheet at row {row_idx + 1}: {row} - Error: {e}")
                continue # ข้ามแถวที่มีปัญหาเรื่องข้อมูล

        return jsonify({"data": products}), 200

    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Error: Stock sheet '{STOCK_SHEET_NAME}' not found.")
        return jsonify({"error": f"Stock sheet '{STOCK_SHEET_NAME}' not found. Please check configuration."}), 500
    except Exception as e:
        app.logger.error(f"Error fetching all stock: {e}", exc_info=True) # เพิ่ม exc_info=True เพื่อพิมพ์ traceback เต็มๆ
        return jsonify({"error": "An error occurred while fetching stock data. Please check server logs."}), 500


@app.route('/api/stock/<product_name>', methods=['GET'])
def api_get_stock_by_name(product_name):
    try:
        gc = get_sheets_client()
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 500

    try:
        sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(STOCK_SHEET_NAME)
        data = sheet.get_all_values()

        if len(data) <= 1:
            return jsonify({"message": f"Product '{product_name}' not found", "data": None}), 404

        for row_idx, row in enumerate(data): # วนลูปทุกแถวรวม Header เพื่อหา
            if row_idx == 0: # ข้าม Header
                continue

            if len(row) < 2: # ตรวจสอบความยาวแถว
                continue # ข้ามแถวที่สั้นเกินไป

            if row[0].strip().lower() == product_name.strip().lower(): # เปรียบเทียบชื่อสินค้าแบบไม่คำนึงถึง Case และตัดช่องว่าง
                stock_quantity_str = row[1].strip()
                stock_quantity = 0
                if stock_quantity_str:
                    stock_quantity = int(stock_quantity_str)

                image_url = row[3].strip() if len(row) > 3 and row[3] else None
                if image_url and not image_url.startswith('https://'):
                    image_url = None

                return jsonify({
                    "data": {
                        "name": row[0].strip(),
                        "quantity": stock_quantity,
                        "imageUrl": image_url
                    }
                }), 200
        
        return jsonify({"message": f"Product '{product_name}' not found", "data": None}), 404

    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Error: Stock sheet '{STOCK_SHEET_NAME}' not found.")
        return jsonify({"error": f"Stock sheet '{STOCK_SHEET_NAME}' not found. Please check configuration."}), 500
    except Exception as e:
        app.logger.error(f"Error fetching stock by name: {e}", exc_info=True)
        return jsonify({"error": "An error occurred while fetching product data. Please check server logs."}), 500


@app.route('/api/stock/update', methods=['POST'])
def api_update_stock():
    try:
        gc = get_sheets_client()
    except ConnectionError as e:
        return jsonify({"error": str(e)}), 500

    data = request.get_json()
    product_name_raw = data.get('productName')
    quantity_change_raw = data.get('quantityChange')
    transaction_type = data.get('transactionType')

    if not all([product_name_raw, quantity_change_raw, transaction_type]):
        return jsonify({"error": "Missing required fields (productName, quantityChange, transactionType)"}), 400

    product_name = product_name_raw.strip()
    try:
        quantity_change = int(quantity_change_raw)
    except ValueError:
        return jsonify({"error": "quantityChange must be an integer."}), 400


    try:
        stock_sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(STOCK_SHEET_NAME)
        transaction_sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(TRANSACTION_SHEET_NAME)
        
        # Find product and update stock
        stock_data = stock_sheet.get_all_values()
        row_index_in_sheet = -1 # แถวใน Google Sheet (เริ่มต้นที่ 1)
        current_stock = 0
        original_product_name = "" # เก็บชื่อสินค้าในรูปแบบเดิมจากชีท

        # วนลูปจากข้อมูลจริง (ข้าม Header)
        for idx, row in enumerate(stock_data):
            if idx == 0: # Skip header row
                continue

            if len(row) < 2: # ตรวจสอบความยาวแถว
                continue

            # เปรียบเทียบแบบไม่คำนึงถึง Case และตัดช่องว่าง
            if row[0].strip().lower() == product_name.lower():
                try:
                    current_stock = int(row[1])
                except ValueError:
                    app.logger.warning(f"Non-numeric stock quantity found for {row[0]} at row {idx+1}. Assuming 0. Value: '{row[1]}'")
                    current_stock = 0 # หากสต็อกไม่ใช่ตัวเลข ให้ถือว่าเป็น 0
                
                row_index_in_sheet = idx + 1 # +1 เพราะ idx เป็น 0-based, sheet row เป็น 1-based
                original_product_name = row[0].strip()
                break
        
        if row_index_in_sheet == -1:
            return jsonify({"error": f"Product '{product_name}' not found in stock."}), 404
        
        new_stock = current_stock + quantity_change

        if transaction_type == "Sell" and new_stock < 0:
            return jsonify({"error": f"Not enough stock for '{original_product_name}'. Available: {current_stock}, trying to sell: {abs(quantity_change)}."}), 400
        
        # Update stock in sheet
        stock_sheet.update_cell(row_index_in_sheet, 2, new_stock) # Column B is 2nd column

        # Log transaction
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Ensure that `quantity_change` is correctly logged as + or - for 'Add' or 'Sell'
        # transaction_sheet.append_row([timestamp, original_product_name, quantity_change, transaction_type, new_stock])
        # ปรับปรุงการบันทึก transaction เพื่อความชัดเจนในชีท
        transaction_sheet.append_row([
            timestamp,
            original_product_name,
            abs(quantity_change), # บันทึกจำนวนที่เป็นบวกเสมอ
            transaction_type,     # ระบุประเภท ("Add" หรือ "Sell")
            new_stock             # สต็อกคงเหลือหลังทำรายการ
        ])


        action_verb = "ขาย" if transaction_type == "Sell" else "เพิ่ม"
        message = f"{action_verb} {abs(quantity_change)} ชิ้น ของ {original_product_name} เรียบร้อยแล้วค่ะ สต๊อกคงเหลือ: {new_stock} ชิ้น"
        
        return jsonify({"message": message, "newStock": new_stock}), 200

    except gspread.exceptions.WorksheetNotFound as e:
        app.logger.error(f"Sheet not found: {e}", exc_info=True)
        return jsonify({"error": f"Required sheet not found. Please check '{STOCK_SHEET_NAME}' and '{TRANSACTION_SHEET_NAME}'."}), 500
    except Exception as e:
        app.logger.error(f"Error updating stock: {e}", exc_info=True)
        return jsonify({"error": f"An error occurred while processing the request: {e}. Please check server logs."}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.getenv('PORT', 8000))) # Changed default port to 8000
