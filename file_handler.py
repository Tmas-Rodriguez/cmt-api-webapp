import os
import shutil
from datetime import datetime
from pathlib import Path


def get_client_name_from_filename(filename):
    """
    Extract client name from filename.
    Client name is the last word before the file extension.
    Example: "company_data.xlsx" -> "data"
    """
    name_without_ext = os.path.splitext(filename)[0]
    parts = name_without_ext.split(' ')
    return parts[-1].lower() if parts else name_without_ext


def get_formatted_date():
    """
    Get today's date in yyyymmdd format.
    """
    return datetime.now().strftime("%Y%m%d")


def create_subfolder_name(filename):
    """
    Create the subfolder name in format: csv_{client-name}_{yyyymmdd}
    """
    client_name = get_client_name_from_filename(filename)
    date_str = get_formatted_date()
    return f"csv_{client_name}_{date_str}"


def save_file_to_organized_folder(file_obj, base_upload_folder):
    """
    Save uploaded file to organized subfolder structure.
    Archives previous folder to "old" folder before creating new one.
    Returns tuple: (success: bool, message: str, file_path: str)
    """
    try:
        filename = file_obj.filename
        
        # Create subfolder name
        subfolder_name = create_subfolder_name(filename)
        subfolder_path = os.path.join(base_upload_folder, subfolder_name)
        old_folder_path = os.path.join(base_upload_folder, "old")
        
        # Create "old" folder if it doesn't exist
        os.makedirs(old_folder_path, exist_ok=True)
        
        # Find and move any existing csv_* folders to "old" (except the one being created)
        for item in os.listdir(base_upload_folder):
            item_path = os.path.join(base_upload_folder, item)
            
            # Check if it's a directory, starts with "csv_", and is not the new folder being created
            if os.path.isdir(item_path) and item.startswith("csv_") and item != subfolder_name:
                try:
                    destination = os.path.join(old_folder_path, item)
                    # Remove destination if it already exists in old
                    if os.path.exists(destination):
                        shutil.rmtree(destination)
                    # Move the folder to old
                    shutil.move(item_path, destination)
                    print(f"DEBUG: Moved {item} to old folder", flush=True)
                except Exception as e:
                    print(f"DEBUG: Error moving {item} to old folder: {str(e)}", flush=True)
        
        # Create new subfolder (remove if it somehow already exists)
        if os.path.exists(subfolder_path):
            shutil.rmtree(subfolder_path)
        os.makedirs(subfolder_path, exist_ok=True)
        
        # Save file to subfolder
        file_path = os.path.join(subfolder_path, filename)
        file_obj.save(file_path)
        
        return True, f"Saved to {subfolder_name}/", file_path
        
    except Exception as e:
        return False, str(e), None
