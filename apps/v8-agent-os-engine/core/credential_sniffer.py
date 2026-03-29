import os
import json
import logging

logger = logging.getLogger(__name__)

class QwenCredentialSniffer:
    """
    Utility to detect and reuse local credentials from Qwen CLI or similar terminal tools.
    This enables a 'Bring Your Own Token' zero-config experience, allowing the engine
    to inherit authorizations granted to tools like qwen-code.
    """
    
    @staticmethod
    def get_qwen_token() -> str | None:
        """
        Attempts to read the Qwen access token from the local filesystem.
        """
        # Cross-platform path resolution
        home_dir = os.path.expanduser("~")
        
        # Common paths for Qwen CLI or Qwen Code credentials
        possible_paths = [
            os.path.join(home_dir, ".qwen", "oauth_creds.json"),
            os.path.join(home_dir, ".qwen-code", "config.json")
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        creds = json.load(f)
                        
                        # Handle oauth_creds.json format
                        if "access_token" in creds:
                            access_token = creds["access_token"]
                            if access_token:
                                logger.info(f"Successfully sniffed Qwen CLI token from {path}")
                                return access_token
                                
                        # Handle potential alternative config.json format
                        if "api_key" in creds:
                            api_key = creds["api_key"]
                            if api_key:
                                logger.info(f"Successfully sniffed Qwen API key from {path}")
                                return api_key
                                
                except Exception as e:
                    logger.warning(f"Failed to read Qwen credentials from {path}: {e}")
                    
        return None
