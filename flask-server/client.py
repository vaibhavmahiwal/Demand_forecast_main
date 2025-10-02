import requests
import json
import datetime

# 🚨 CHANGE 1: Update URL to the new unified API endpoint
# The Flask app runs on port 5002 (as specified in app.py) and the route is /api/projects
FLASK_SERVER_URL = "http://localhost:5002/api/projects"

def get_all_predictions_and_save(project_data):
    """
    Sends a payload to the Flask server's unified endpoint to run predictions
    AND save the project data to the database.
    
    Args:
        project_data (dict): A dictionary containing project features.
    """
    
    # 🚨 CHANGE 2: Structure the payload to match the new Flask POST handler
    # The new Flask endpoint expects 'input_features' for ML, and 'project_details' for DB save.
    payload = {
        "input_features": {
            "budget": project_data["budget"],
            "location": project_data["location"],
            "towerType": project_data["towerType"],
            "substationType": project_data["substationType"],
            "geo": project_data["geo"],
            "taxes": project_data["taxes"],
        },
        "project_details": {
            # These fields are required by the Project database model
            "createdBy": project_data["createdBy"],
            "status": project_data["status"],
            "createdAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    }
    
    print(f"Sending POST request to: {FLASK_SERVER_URL}")
    print("--- Payload ---")
    print(json.dumps(payload, indent=4))

    try:
        response = requests.post(FLASK_SERVER_URL, json=payload)
        response.raise_for_status()
        
        server_response = response.json()
        
        # 🚨 CHANGE 3: Response now contains the created 'project' object with forecasts
        print("\n✅ Success! Project Created and Forecasted:")
        print(json.dumps(server_response, indent=4))
        
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        try:
            error_data = response.json()
            print("Server Error Details:", json.dumps(error_data, indent=4))
        except:
            print("Could not decode error response from server.")
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Error connecting to the Flask server: {e}")
    except json.JSONDecodeError:
        print("\n❌ Error decoding JSON from the server response.")

if __name__ == "__main__":
    # Example project data, now including DB fields like 'createdBy' and 'status'
    # These mimic the data sent by the handleCreateProject function in your React app.
    new_project_data = {
        "budget": "10000000",
        "towerType": "400 kV", 
        "taxes": "18% GST",
        "location": "Bengaluru",
        "substationType": "AIS (Air Insulated Substation)",
        "geo": "urban",
        
        # New fields for database
        "createdBy": "test.employee@company.com", 
        "status": "pending",
    }
    
    get_all_predictions_and_save(new_project_data)