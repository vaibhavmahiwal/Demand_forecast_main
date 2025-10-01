from flask import Flask, request, jsonify
from flask_cors import CORS 
import onnxruntime as ort
import numpy as np
import os
import joblib

# Create a Flask web server instance.
app = Flask(__name__)
CORS(app) # Enable CORS for all origins

# Define a mapping for ALL 7 models and their corresponding files
MODEL_ASSET_MAPPING = {
    "steel": {
        "onnx": "steel_model.onnx",
        "scaler": "scaler_steel.joblib",
        "columns": "model_columns_steel.joblib",
    },
    "conductor": {
        "onnx": "conductor_model.onnx",
        "scaler": "scaler_conductor.joblib",
        "columns": "model_columns_conductor.joblib",
    },
    "transformers": {
        "onnx": "transformers_model.onnx",
        "scaler": "scaler_transformers.joblib",
        "columns": "model_columns_transformers.joblib",
    },
    "earthwire": {
        "onnx": "earthwire_model.onnx",
        "scaler": "scaler_earthwire.joblib",
        "columns": "model_columns_earthwire.joblib",
    },
    "foundation": {
        "onnx": "foundation_model.onnx",
        "scaler": "scaler_foundation.joblib",
        "columns": "model_columns_foundation.joblib",
    },
    "reactors": {
        "onnx": "reactors_model.onnx",
        "scaler": "scaler_reactors.joblib",
        "columns": "model_columns_reactors.joblib",
    },
    "tower": {
        "onnx": "tower_model.onnx",
        "scaler": "scaler_tower.joblib",
        "columns": "model_columns_tower.joblib",
    },
}

# Dictionary to hold the loaded models, scalers, and column names
LOADED_MODELS = {}

# Load all assets once when the app starts.
print("--- Loading All 7 Models ---")
for model_name, paths in MODEL_ASSET_MAPPING.items():
    try:
        session = ort.InferenceSession(paths["onnx"])
        scaler = joblib.load(paths["scaler"])
        columns = joblib.load(paths["columns"])
        
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        
        LOADED_MODELS[model_name] = {
            "session": session,
            "scaler": scaler,
            "columns": columns,
            "input_name": input_name,
            "output_name": output_name,
        }
        print(f"✅ Successfully loaded assets for: {model_name.upper()}")

    except Exception as e:
        print(f"❌ Error loading assets for {model_name.upper()}. Check files: {e}")

if not LOADED_MODELS:
    print("FATAL: No models were loaded successfully. Exiting.")
    exit(1)
print("--- All necessary models loaded ---")

# --- Feature Mapping and Engineering Function ---
def create_feature_vector(input_data, columns):
    """
    Creates a feature vector for prediction based on client input, 
    using the specific column list for the selected model.
    """
    feature_vector = {col: 0.0 for col in columns}

    try:
        # Numerical Features
        if "towerType" in input_data:
            tower_kv = float(input_data["towerType"].split(' ')[0])
            if 'Voltage_kV' in feature_vector:
                feature_vector['Voltage_kV'] = tower_kv
        
        if "budget" in input_data:
            if 'Estimated_Cost_Million' in feature_vector:
                feature_vector['Estimated_Cost_Million'] = float(input_data["budget"]) / 1000000.0
        
    except (ValueError, IndexError) as e:
        raise ValueError(f"Required numeric data malformed: {e}")
        
    # One-Hot Encoding (OHE) for categorical features
    if "location" in input_data:
        location_key = f"Location_ {input_data['location']}"
        if location_key in feature_vector:
            feature_vector[location_key] = 1.0
    
    if "substationType" in input_data:
        substation_key = f"Substation_Type_ {input_data['substationType']}"
        substation_key = substation_key.replace(" (", "_(").replace(" ", "_") 
        if substation_key in feature_vector:
            feature_vector[substation_key] = 1.0

    if "geo" in input_data:
        geo_key = f"Geographical_Zone_ {input_data['geo']}"
        if geo_key in feature_vector:
            feature_vector[geo_key] = 1.0

    if "taxes" in input_data:
        taxes_key = f"Taxes_Applicable_{input_data['taxes']}"
        if taxes_key in feature_vector:
            feature_vector[taxes_key] = 1.0
        
    final_features = [feature_vector[col] for col in columns]
    
    return final_features

@app.route("/predict_all", methods=["POST"])
def predict_all():
    """
    Handles a single prediction request and returns predictions from all models.
    """
    try:
        if not request.is_json:
            return jsonify({"error": "Request body must be JSON."}), 400

        data = request.get_json()
        input_features = data.get("input_features")
        if not input_features:
            return jsonify({"error": "Missing 'input_features' in JSON payload."}), 400

        all_predictions = {}
        
        for model_name, model_assets in LOADED_MODELS.items():
            try:
                features_ordered = create_feature_vector(input_features, model_assets["columns"])
            
                input_array = np.array(features_ordered, dtype=np.float32).reshape(1, -1)
                scaled_input = model_assets["scaler"].transform(input_array)
                
                prediction = model_assets["session"].run(
                    [model_assets["output_name"]], 
                    {model_assets["input_name"]: scaled_input}
                )
                prediction_result = float(prediction[0].flatten()[0])
                
                all_predictions[model_name] = prediction_result
            
            except Exception as e:
                print(f"Error predicting for {model_name}: {e}")
                all_predictions[model_name] = "Prediction Error" # Report the error to the user

        return jsonify(all_predictions) 

    except ValueError as e:
        return jsonify({"error": f"Input data error: {str(e)}"}), 400
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

if __name__ == "__main__":
    if LOADED_MODELS:
        app.run(debug=True, host="0.0.0.0", port=5002)
    else:
        print("Application startup failed due to model loading error.")