import joblib
import requests
import sqlite3
import pandas as pd
import os
import re
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelBinarizer



# Function to clean the stock symbol
def clean_symbol(symbol):
    return symbol.split('.')[0]  # Remove anything after and including a dot


# Function to clean table name
def clean_table_name(filepath):
    filename = os.path.basename(filepath)  # Extract filename
    stock_name = filename.split('_')[0]  # Extract stock symbol
    table_name = f"{stock_name.lower()}"  # Create table name
    return re.sub(r'\W|^(?=\d)', '_', table_name)  # Sanitize name


# Function to integrate DataFrame into SQLite database
def dataframe_to_sqlite(df, db_name, table_name):
    conn = sqlite3.connect(db_name)
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    print(f"Data successfully integrated into the {table_name} table in {db_name}.")


# Function to modify the data
def modify_data(csv_file):
    # Load CSV data into a Pandas DataFrame
    df = pd.read_csv(csv_file)
    
    # Ensure the data is sorted by timestamp for proper shifting
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
        # Calculate rolling averages
        rolling_averages = df[["close"]].rolling(horizon).mean()
        
        # Calculate close ratio columns
        ratio_column = f"close_ratio_{horizon}"
        df[ratio_column] = df["close"] / rolling_averages["close"]
        
        # Calculate trend columns
        trend_column = f"trend_{horizon}"
        df[trend_column] = df[["close"]].shift(1).rolling(horizon).sum()
        
        # Add new columns to predictors list
        new_predictors += [ratio_column, trend_column]

    columns_to_check = df.columns.difference(['tomorrow'])  # All columns except 'tomorrow'
    df = df.dropna(subset=columns_to_check)

    return df


# Function to pull data from API and save to file
def data_pull():
    # Ask the user for the stock symbol
    symbol = input("Enter the stock symbol (e.g., IBM, AAPL, ASML.AS): ").strip().upper()

    # Clean the symbol to remove suffix
    clean_symbol_name = clean_symbol(symbol)

    # Filepath where CSV will be saved
    data_folder = r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\data"
    os.makedirs(data_folder, exist_ok=True)
    filepath = os.path.join(data_folder, f"{clean_symbol_name}_daily_stock.csv")

    # Determine yesterday's date
    yesterday = (datetime.now() - timedelta(days=1)).date()

    # Check if the file already exists
    if os.path.exists(filepath):
        # Load existing data
        df_existing = pd.read_csv(filepath)
        df_existing["timestamp"] = pd.to_datetime(df_existing["timestamp"])

        latest_date = df_existing["timestamp"].max().date()
        if latest_date >= yesterday:
            print(f"Data for {symbol} is already up-to-date.")
            return
        else:
            print(f"Updating data for {symbol}...")
    else:
        print(f"No data found for {symbol}. Fetching full dataset...")
        df_existing = None  # No existing data

    # Fetch data from API
    api_key_path = r"C:\Users\gianl\.vscode\FinalProject\key.txt"
    with open(api_key_path, "r") as file:
        api_key = file.read().strip()
    url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={api_key}&datatype=csv&outputsize=full"
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Save the content to the file with cleaned symbol name
        with open(filepath, 'wb') as file:
            file.write(response.content)

        # Load the newly downloaded data
        df_new = pd.read_csv(filepath)
        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'])

        # Combine with existing data, if any
        if df_existing is not None:
            df_combined = pd.concat([df_existing, df_new]).drop_duplicates(subset='timestamp').sort_values('timestamp')
        else:
            df_combined = df_new

        # Save the combined data back to the file
        df_combined.to_csv(filepath, index=False)
        print(f"Data for {symbol} updated successfully and saved to {filepath}")

        # Modify the combined data and save to SQLite
        modified_df = modify_data(filepath)
        db_name = "finance.db"
        dataframe_to_sqlite(modified_df, db_name, clean_symbol_name)
    else:
        print(f"Failed to fetch data for {symbol}. Status code: {response.status_code}")


# Function to retrieve stock data from SQLite
def get_stock_data(db_name, table_name):
    conn = sqlite3.connect(db_name)
    query = f"SELECT * FROM {table_name}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# Function to predict using a trained model
def predict(train, test, predictors, model):
    model.fit(train[predictors], train["target"])
    preds = model.predict(test[predictors])
    preds[preds >= .6] = 1
    preds[preds <  .6] = 0
    probabilities = model.predict_proba(test[predictors]) ##new
    preds = pd.Series(preds, index=test.index, name="Predictions")
    confidence = pd.Series(probabilities.max(axis=1), index=test.index, name="Confidence") ##new
    combined = pd.concat([test["target"], preds, confidence], axis=1)   ## added confi to list
    return combined

# Backtesting function
def backtest(data, model, predictors, start=2500, step=250):
    all_predictions = []

    for i in range(start, data.shape[0], step):
        train = data.iloc[0:i].copy()
        test = data.iloc[i:(i+step)].copy()
        predictions = predict(train, test, predictors, model)
        all_predictions.append(predictions)
    
    return pd.concat(all_predictions)

# Build model function
def build_model():
    # Ask the user for the stock symbol
    symbol = input("Enter the stock symbol (e.g., IBM, AAPL): ").strip().upper()
    
    # Database and table setup
    db_name = "finance.db"
    table_name = f"{symbol.upper()}"

    # Retrieve data from SQLite
    try:
        df = get_stock_data(db_name, table_name)
    except Exception as e:
        print(f"Error retrieving data for {symbol}: {e}")
        return

    # Check if necessary columns are present
    required_columns = ["target", "open", "high", "low", "close", "volume"]
    if not all(col in df.columns for col in required_columns):
        print(f"Missing required columns in data for {symbol}. Cannot build model.")
        return

    # Train-test split
    train = df.iloc[:-100]
    test = df.iloc[-100:]

    # Predictors to use
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

    # Save the model to the models directory
    model_dir = r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\models"
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, f"{symbol}_model.pkl")
    joblib.dump(model, model_path)
    print(f"Model for {symbol} saved successfully at {model_path}.")


def fetch_current_data(symbol):
    """
    Fetch the most recent data for the current day from the database.
    """
    # Define database and table name
    db_name = "finance.db"
    table_name = f"{symbol.upper()}"
    
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect(db_name)
        
        # Get today's date
        today_date = (datetime.now() - timedelta(days=1)).date()    #####################################################################TODO change to pull latest data from api and update date to current date  --datetime.now().date()
        
        print(f"{today_date}")
        # Query for the most recent data
        query = f"""
        SELECT * FROM {table_name} 
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp DESC LIMIT 1
        """
        df = pd.read_sql_query(query, conn, params=(today_date,))
        conn.close()
        
        if df.empty:
            print(f"No data available for {symbol} for today's date ({today_date}).")
            return None
        
        return df
    except Exception as e:
        print(f"Error fetching current day data for {symbol}: {e}")
        return None


def predict_current_day(symbol):
    #loading model
    model_dir = r"C:\Users\gianl\.vscode\FinalProject\StockTradingBot\models"
    model_path = os.path.join(model_dir, f"{symbol.upper()}_model.pkl")

    if not os.path.exists(model_path):
        print(f"Model of {symbol} not trained yet.")
        return
    
    model = joblib.load(model_path)

    # load data of today
    current_day_data = fetch_current_data(symbol)
    if current_day_data is None:
        return
    
    db_name = "finance.db"
    table_name = f"{symbol.upper()}"
    try:
        df = get_stock_data(db_name, table_name)
    except Exception as e:
        print(f"Error loading data of {symbol}: {e}")
        return
    
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
        #return
    
    # pred = prediction; prob = probability calculation
    pred = model.predict(current_day_data)
    prob = model.predict_proba(current_day_data).max(axis=1)

    print(f"Prediction for the current day ({current_day_data.index[0]}: {'Rise' if pred[0] == 1 else 'Drop'})")
    print(f"Confidence:  {prob[0]: .2%}")
    return {"date": current_day_data.index[0], "prediction" : 'Rise' if pred[0] == 1 else 'Drop', 'confidence': prob[0]}
    

# Main loop
if __name__ == "__main__":
    print("Options: data_pull, build_model, current_day")
    option = input("Select: ").strip().lower()

    # Pull historical data from API
    if option == "data_pull":
        data_pull()
    
    # Build ml model based on historical data
    if option == "build_model":
        build_model()

    if option == "current_day":
        symbol = input("Enter stock symbol (e.g., IBM, AAPL): ").strip().lower()
        predict_current_day(symbol)