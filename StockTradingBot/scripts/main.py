import json
import joblib
import requests
import sqlite3
import pandas as pd
import os
import re
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier
#from sklearn.preprocessing import LabelBinarizer

# Define base directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "StockTradingBot", "models")
LOG_DIR = os.path.join(BASE_DIR, "StockTradingBot", "logs")
SCRIPT_DIR = os.path.join(BASE_DIR, "StockTradingBot", "scripts")
KEY_PATH = os.path.join(BASE_DIR, "key.txt")
STOCK_SYMBOLS_PATH = os.path.join(BASE_DIR, "stock_symbols.txt")
DB_PATH = os.path.join(BASE_DIR, "finance.db")

# Ensure necessary directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)


def clean_symbol(symbol):
    return symbol.split('.')[0]  



def clean_table_name(filepath):
    filename = os.path.basename(filepath)  
    stock_name = filename.split('_')[0]  
    table_name = f"{stock_name.lower()}"  
    return re.sub(r'\W|^(?=\d)', '_', table_name)  


# df into database
def dataframe_to_sqlite(df, db_name, table_name):
    conn = sqlite3.connect(db_name)
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    print(f"Data successfully integrated into the {table_name} table in {db_name}.")


# Function to modify the data
def modify_data(csv_file):
    
    df = pd.read_csv(csv_file)
    df = df.sort_values('timestamp')
    
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # Add 'tomorrow' column (shifted 'close' column)
    df['tomorrow'] = df['close'].shift(-1)

    # Add 'target' column (win or loss compared to yesterday)
    df["target"] = (df["tomorrow"] > df["close"]).astype(int)
    
    horizons = [2, 5, 60, 250, 500]
    new_predictors = []
    
    for horizon in horizons:
        # rolling averages
        rolling_averages = df[["close"]].rolling(horizon).mean()

        ratio_column = f"close_ratio_{horizon}"
        df[ratio_column] = df["close"] / rolling_averages["close"]

        trend_column = f"trend_{horizon}"
        df[trend_column] = df[["close"]].shift(1).rolling(horizon).sum()
        
        # new features
        new_predictors += [ratio_column, trend_column]

    columns_to_check = df.columns.difference(['tomorrow'])  # All columns except 'tomorrow'
    df = df.dropna(subset=columns_to_check)

    return df


# pull data from API and save to file
def data_pull(symbol=None):
    if symbol is None:
        symbol = input("Enter the stock symbol (e.g., IBM, AAPL, ASML.AS): ").strip().upper()
    clean_symbol_name = clean_symbol(symbol)

    data_folder = DATA_DIR #r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\data"
    #os.makedirs(data_folder, exist_ok=True)
    filepath = os.path.join(data_folder, f"{clean_symbol_name}_daily_stock.csv")

    # yesterday's date
    yesterday = (datetime.now() - timedelta(days=1)).date()

    if os.path.exists(filepath):
        df_existing = pd.read_csv(filepath)
        df_existing["timestamp"] = pd.to_datetime(df_existing["timestamp"])

        latest_date = df_existing["timestamp"].max().date()
        if latest_date >= yesterday:
            print(f"Data for {symbol} is already up-to-date.")
            return True  
        else:
            print(f"Updating data for {symbol}...")
    else:
        print(f"No data found for {symbol}. Fetching full dataset...")
        df_existing = None  

    # Fetch data from API
    api_key_path = r"C:\Users\gianl\.vscode\FinalProject\key.txt"
    with open(api_key_path, "r") as file:
        api_key = file.read().strip()
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={api_key}&datatype=csv&outputsize=full"
    response = requests.get(url)

    # successful
    if response.status_code == 200:
        with open(filepath, 'wb') as file:
            file.write(response.content)

        df_new = pd.read_csv(filepath)
        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'])

        if df_existing is not None:
            df_combined = pd.concat([df_existing, df_new]).drop_duplicates(subset='timestamp').sort_values('timestamp')
        else:
            df_combined = df_new

        df_combined.to_csv(filepath, index=False)
        print(f"Data for {symbol} updated successfully and saved to {filepath}")

        # Modify data and save to db
        modified_df = modify_data(filepath)
        db_name = "finance.db"
        dataframe_to_sqlite(modified_df, db_name, clean_symbol_name)
        return True  
    else:
        print(f"Failed to fetch data for {symbol}. Status code: {response.status_code}")
        return False 


# retrieve stock data from db
def get_stock_data(db_name, table_name):
    conn = sqlite3.connect(db_name)
    query = f"SELECT * FROM {table_name}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# predict using a trained model
def predict(train, test, predictors, model):
    model.fit(train[predictors], train["target"])
    preds = model.predict(test[predictors])
    preds[preds >= .6] = 1
    preds[preds <  .6] = 0
    probabilities = model.predict_proba(test[predictors]) 
    preds = pd.Series(preds, index=test.index, name="Predictions")
    confidence = pd.Series(probabilities.max(axis=1), index=test.index, name="Confidence") 
    combined = pd.concat([test["target"], preds, confidence], axis=1)   ## added confi to list
    return combined

# Backtesting 
def backtest(data, model, predictors, start=2500, step=250):
    all_predictions = []

    for i in range(start, data.shape[0], step):
        train = data.iloc[0:i].copy()
        test = data.iloc[i:(i+step)].copy()
        predictions = predict(train, test, predictors, model)
        all_predictions.append(predictions)
    
    return pd.concat(all_predictions)

# Build model function
def build_model(symbol=None):
    if symbol is None:
        symbol = input("Enter the stock symbol (e.g., IBM, AAPL): ").strip().upper()
    else:
        symbol = symbol.strip().upper()
    
    # Database and table setup
    db_name = "finance.db"
    table_name = f"{symbol.upper()}"

    try:
        df = get_stock_data(db_name, table_name)
    except Exception as e:
        print(f"Error retrieving data for {symbol}: {e}")
        return False

    required_columns = ["target", "open", "high", "low", "close", "volume"]
    if not all(col in df.columns for col in required_columns):
        print(f"Missing required columns in data for {symbol}. Cannot build model.")
        return False

    # Train-test split
    train = df.iloc[:-100]
    test = df.iloc[-100:]

    predictors = ["open", "high", "low", "close", "volume"]
    rolling_predictors = [col for col in df.columns if "close_ratio_" in col or "trend_" in col]
    all_predictors = predictors + rolling_predictors


    # Initialize the model
    model = RandomForestClassifier(n_estimators=200, min_samples_split=50, random_state=1)
    # Backtest the model
    predictions = backtest(df, model, all_predictors)
    # Evaluate model performance
    accuracy = (predictions["target"] == predictions["Predictions"]).mean()
    print(f"Model Accuracy for {symbol}: {accuracy:.2%}")


    model_dir = MODEL_DIR #r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\models"
    #os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, f"{symbol}_model.pkl")
    joblib.dump(model, model_path)
    print(f"Model for {symbol} saved successfully at {model_path}.")
    return True


def fetch_current_data(symbol):
    """
    Fetch the most recent data for the current day from the database.
    """

    db_name = "finance.db"
    table_name = f"{symbol.upper()}"
    
    try:
        conn = sqlite3.connect(db_name)
        
        # Get yesterdays date
        today_date = (datetime.now() - timedelta(days=1)).date()    #TODO currently has a error on mondays, no data for weekends
        
        print(f"{today_date}")
        # Query for date
        query = f"""
        SELECT * FROM {table_name} 
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp DESC LIMIT 1
        """
        df = pd.read_sql_query(query, conn, params=(today_date,))
        conn.close()
        
        if df.empty:
            print(f"No data available for {symbol} for today's date ({today_date}) Line 224 in main.")
            return None
        
        return df
    except Exception as e:
        print(f"Error fetching current day data for {symbol}: {e}")
        return None


def predict_current_day(symbol):
    #loading model
    model_dir = MODEL_DIR #r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\models"
    model_path = os.path.join(model_dir, f"{symbol.upper()}_model.pkl")

    if not os.path.exists(model_path):
        print(f"Model of {symbol} not trained yet.")
        return None
    
    model = joblib.load(model_path)

    current_day_data = fetch_current_data(symbol)
    if current_day_data is None:
        return None
    
    db_name = "finance.db"
    table_name = f"{symbol.upper()}"
    try:
        df = get_stock_data(db_name, table_name)
    except Exception as e:
        print(f"Error loading data of {symbol}: {e}")
        return None
    
    predictors = ["open", "high", "low", "close", "volume"]
    rolling_predictors = [col for col in df.columns if "close_ratio_" in col or "trend_" in col]
    all_predictors = predictors + rolling_predictors

    for horizon in [2,5,60,250,500]:
        rolling_averages = df[["close"]].rolling(horizon).mean()
        current_day_data[f"close_ratio_{horizon}"] = current_day_data["close"] / rolling_averages["close"].iloc[-1]
        current_day_data[f"trend_{horizon}"] = df[["close"]].shift(1).rolling(horizon).sum().iloc[-1]

    current_day_data = current_day_data[all_predictors]
    if current_day_data.isnull().any().any():
        print("data for yesterday")

    

    pred = model.predict(current_day_data)
    prob = model.predict_proba(current_day_data).max(axis=1)

    print(f"Prediction for the current day ({current_day_data.index[0]}: {'Rise' if pred[0] == 1 else 'Drop'})")
    print(f"Confidence:  {prob[0]: .2%}")
    return {"date": current_day_data.index[0], "prediction": 'Rise' if pred[0] == 1 else 'Drop', 'confidence': prob[0]}


def evaluate_all_stocks():
    """
    Evaluate all stocks in the stock_symbols.txt file:
    - Fetch data if needed
    - Create model if it doesn't exist
    - Get prediction for the current day
    - Store results in JSON file
    """

    symbols_file = STOCK_SYMBOLS_PATH #r"C:\Users\gianl\.vscode\FinalProject\stock_symbols.txt"
    log_dir = LOG_DIR #r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\logs"
    json_log_file = os.path.join(log_dir, "predictions_log.json")
    
    #os.makedirs(log_dir, exist_ok=True)
    
    try:
        with open(symbols_file, 'r') as f:
            stock_symbols = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Stock symbols file not found at {symbols_file}")
        return
    except Exception as e:
        print(f"Error reading stock symbols file: {e}")
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    batch_data = {
        "batch_id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "timestamp": timestamp,
        "predictions": []
    }

    for symbol in stock_symbols:
        symbol = symbol.strip().upper()
        print(f"\nProcessing {symbol}...")
        
        status = "Complete"
        prediction_data = {
            "symbol": symbol,
            "prediction": "N/A",
            "confidence": "N/A",
            "status": status
        }
        
        try:
            # Fetch data if needed
            try:
                data_success = data_pull(symbol)
                if not data_success:
                    prediction_data["status"] = "Failed to fetch data"
                    batch_data["predictions"].append(prediction_data)
                    continue
                    
            except Exception as e:
                prediction_data["status"] = f"Error fetching data: {str(e)[:50]}"
                batch_data["predictions"].append(prediction_data)
                continue
            
            # Check if model exists, if not, build it
            model_dir = MODEL_DIR #r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\models"
            model_path = os.path.join(model_dir, f"{symbol}_model.pkl")
            
            if not os.path.exists(model_path):
                print(f"Model for {symbol} not found. Building new model...")
                model_success = build_model(symbol)
                if not model_success:
                    prediction_data["status"] = "Failed to build model"
                    batch_data["predictions"].append(prediction_data)
                    continue
            
            # Make prediction for current day
            prediction_result = predict_current_day(symbol)
            if prediction_result is None:
                prediction_data["status"] = "Failed to make prediction"
                batch_data["predictions"].append(prediction_data)
                continue
            
            # Add prediction to data
            prediction_data["prediction"] = prediction_result["prediction"]
            prediction_data["confidence"] = f"{prediction_result['confidence']:.2%}"
            prediction_data["status"] = "Complete"
            batch_data["predictions"].append(prediction_data)
                
        except Exception as e:
            prediction_data["status"] = f"Error: {str(e)[:50]}"
            batch_data["predictions"].append(prediction_data)
            print(f"Error processing {symbol}: {e}")
    
    all_batches = []
    if os.path.exists(json_log_file) and os.path.getsize(json_log_file) > 0:
        try:
            with open(json_log_file, 'r') as f:
                all_batches = json.load(f)
                if not isinstance(all_batches, list):
                    all_batches = [all_batches]
        except json.JSONDecodeError:
            all_batches = []
    
    all_batches.append(batch_data)
    
    with open(json_log_file, 'w') as f:
        json.dump(all_batches, f, indent=2)
    
    print(f"\nEvaluation complete. Results saved to {json_log_file}")

    return batch_data


# Main loop
if __name__ == "__main__":
    print("Options: data_pull, build_model, current_day, evaluate_all")
    option = input("Select: ").strip().lower()

    # Pull historical data from API
    if option == "data_pull":
        data_pull()
    
    # Build ml model based on historical data
    elif option == "build_model":
        build_model()

    # Predict for current day
    elif option == "current_day":
        symbol = input("Enter stock symbol (e.g., IBM, AAPL): ").strip().upper()
        predict_current_day(symbol)
    
    # Evaluate all stocks in the list
    elif option == "evaluate_all":
        evaluate_all_stocks()
    
    else:
        print("Invalid option selected: data_pull, build_model, current_day, evaluate_all")