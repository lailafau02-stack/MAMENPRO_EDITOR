# FILE: engine/core/layer_base.py

class Transform:
    """Class sederhana untuk menyimpan properti posisi/skala/rotasi"""
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.scale = 1.0
        self.rotation = 0.0
        self.opacity = 1.0

class Layer:
    """Base Class untuk semua Effect Layer"""
    def __init__(self, z_index=0, enabled=True, name="Layer"):
        self.z_index = z_index
        self.enabled = enabled
        self.name = name
        
        # Setiap layer wajib punya transform
        self.transform = Transform()
        
        # Timing default
        self.start_time = 0.0
        self.end_time = 5.0

    def get_bbox(self):
        return (0, 0, 0, 0)