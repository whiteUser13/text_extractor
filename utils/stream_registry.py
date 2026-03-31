_stream_registry: dict = {}

def register_stream(stream_id: str, info: dict):
    _stream_registry[stream_id] = info

def get_stream_info(stream_id: str):
    return _stream_registry.get(stream_id)

def stream_keys():
    return list(_stream_registry.keys())