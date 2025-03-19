from flask import Flask, jsonify
import json
import os
from datetime import datetime
import threading

from main import evaluate_all_stocks

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "StockTradingBot", "logs")
JSON_LOG_FILE = os.path.join(LOGS_DIR, "predictions_log.json")

def get_latest_predictions():
    """
    Get the latest stock predictions from the JSON log file.
    
    Returns:
        dict: The latest batch of predictions or an error message
    """
    #json_log_file = os.path.join(r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\logs", "predictions_log.json")
    
    try:
        if not os.path.exists(JSON_LOG_FILE) or os.path.getsize(JSON_LOG_FILE) == 0:
            return {"error": "No prediction data available"}
        
        with open(JSON_LOG_FILE, 'r') as f:
            all_batches = json.load(f)
        
        # Get the latest batch
        if not all_batches:
            return {"error": "No batches found in log file"}
        
        latest_batch = all_batches[-1]
        
        return {
            "date": latest_batch["timestamp"],
            "batch_id": latest_batch["batch_id"],
            "predictions": latest_batch["predictions"]
        }
        
    except FileNotFoundError:
        return {"error": "Log file not found"}
    except json.JSONDecodeError:
        return {"error": "Invalid JSON format in log file"}
    except Exception as e:
        return {"error": f"Error retrieving predictions: {str(e)}"}
    
@app.route("/evaluate", methods=["GET"])
def evaluate():
    """
    API endpoint 
    running evaluation and return results
    """
    # Start evaluation in a background thread
    thread = threading.Thread(target=evaluate_all_stocks)
    thread.daemon = True
    thread.start()

    return jsonify(get_latest_predictions())

@app.route("/predictions", methods=["GET"])
def get_predictions():
    """
    API endpoint to get the latest predictions without running a new evaluation
    """
    return jsonify(get_latest_predictions())

if __name__ == "__main__":
    print("\033[31mAPI running...\033[0m")
    app.run(host="0.0.0.0", port=5000, debug=True)
