import yaml
import os

# Load configuration from YAML file
def load_config(config_path="config.yaml"): 
    with open(config_path, "r") as file:
        return yaml.safe_load(file)

# Load the config
config = load_config()

def get_file_path(base_key, file_key):
    """
    Constructs a full path based on base and file keys from the config.
    Args:
        base_key: Key for the base path in the config (e.g., 'database_path').
        file_key: Key for the specific file in the config (e.g., 'grid_file').
    Returns:
        Full path as a string.
    """
    base_path = config.get(base_key, "")
    file_name = config.get(file_key, "")
    return os.path.join(base_path, file_name)
