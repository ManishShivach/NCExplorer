"""
Layer Property Management System for GeoCanvas
==============================================

This module combines all property management functionality including
the core property classes, property manager, and UI widgets.
"""

import os
import json
import datetime
from typing import Any, Dict, List, Optional, Union
from PyQt6.QtWidgets import *
from PyQt6.QtCore import QObject, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette, QFont

# Core Property Classes
class LayerMetadata:
    """Container for layer metadata information."""

    def __init__(self):
        self.name = ""
        self.layer_type = ""  # vector, raster, netcdf
        self.source_file = ""
        self.description = ""
        self.creation_date = datetime.datetime.now().isoformat()
        self.modification_date = datetime.datetime.now().isoformat()
        self.crs = ""
        self.no_data_value = None
        self.statistics = {}  # min, max, mean, std for rasters
        self.attributes = {}  # For vector data attributes
        self.data_type = ""  # int16, float32, etc.
        self.file_size = 0
        self.geometry_type = ""  # Point, LineString, Polygon, etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "layer_type": self.layer_type,
            "source_file": self.source_file,
            "description": self.description,
            "creation_date": self.creation_date,
            "modification_date": self.modification_date,
            "crs": self.crs,
            "no_data_value": self.no_data_value,
            "statistics": self.statistics,
            "attributes": self.attributes,
            "data_type": self.data_type,
            "file_size": self.file_size,
            "geometry_type": self.geometry_type
        }

    def from_dict(self, data: Dict[str, Any]):
        """Load from dictionary."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def update_modification_date(self):
        """Update modification date to current time."""
        self.modification_date = datetime.datetime.now().isoformat()

class LayerDimensions:
    """Container for layer dimension properties."""

    def __init__(self):
        self.width = 0
        self.height = 0
        self.depth = 0  # For 3D data
        self.extent = []  # [minx, maxx, miny, maxy]
        self.pixel_size_x = 0.0
        self.pixel_size_y = 0.0
        self.crs = ""
        self.transform = None  # Affine transform
        self.dimensions = {}  # Custom dimensions e.g., {"time": [...], "level": [...]}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "extent": self.extent,
            "pixel_size_x": self.pixel_size_x,
            "pixel_size_y": self.pixel_size_y,
            "crs": self.crs,
            "transform": str(self.transform) if self.transform else None,
            "dimensions": self.dimensions
        }

    def from_dict(self, data: Dict[str, Any]):
        """Load from dictionary."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_area(self) -> float:
        """Calculate area of the layer."""
        if len(self.extent) >= 4:
            return (self.extent[1] - self.extent[0]) * (self.extent[3] - self.extent[2])
        return 0.0

    def get_center(self) -> tuple:
        """Get center coordinates."""
        if len(self.extent) >= 4:
            center_x = (self.extent[0] + self.extent[1]) / 2
            center_y = (self.extent[2] + self.extent[3]) / 2
            return (center_x, center_y)
        return (0.0, 0.0)

class LayerStyleProperties:
    """Container for layer styling properties."""

    def __init__(self):
        # Common properties
        self.transparency = 0.0  # 0.0 to 1.0
        self.visible = True

        # Vector styling
        self.color = "#FF0000"  # Default red
        self.fill_color = "#FF000080"  # Semi-transparent red
        self.edge_color = "#000000"  # Black
        self.line_width = 1.0
        self.line_style = "solid"  # solid, dashed, dotted
        self.marker_style = "circle"  # circle, square, triangle, etc.
        self.marker_size = 5.0

        # Raster styling
        self.colormap = "viridis"  # matplotlib colormap name
        self.reverse_colormap = False
        self.vmin = None  # Minimum value for colormap
        self.vmax = None  # Maximum value for colormap
        self.interpolation = "nearest"  # nearest, bilinear, bicubic

        # Advanced styling
        self.blend_mode = "normal"  # normal, multiply, screen, etc.
        self.brightness = 0.0  # -1.0 to 1.0
        self.contrast = 0.0  # -1.0 to 1.0
        self.saturation = 0.0  # -1.0 to 1.0
        self.hue_shift = 0.0  # -180 to 180 degrees

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "transparency": self.transparency,
            "visible": self.visible,
            "color": self.color,
            "fill_color": self.fill_color,
            "edge_color": self.edge_color,
            "line_width": self.line_width,
            "line_style": self.line_style,
            "marker_style": self.marker_style,
            "marker_size": self.marker_size,
            "colormap": self.colormap,
            "reverse_colormap": self.reverse_colormap,
            "vmin": self.vmin,
            "vmax": self.vmax,
            "interpolation": self.interpolation,
            "blend_mode": self.blend_mode,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "saturation": self.saturation,
            "hue_shift": self.hue_shift
        }

    def from_dict(self, data: Dict[str, Any]):
        """Load from dictionary."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def validate_transparency(self, value: float) -> float:
        """Validate transparency value."""
        return max(0.0, min(1.0, float(value)))

    def validate_line_width(self, value: float) -> float:
        """Validate line width value."""
        return max(0.1, float(value))

    def validate_marker_size(self, value: float) -> float:
        """Validate marker size value."""
        return max(0.1, float(value))

class NetCDFProperties:
    """Container for NetCDF-specific properties."""

    def __init__(self):
        self.variables = []  # List of available variables
        self.current_variable = None
        self.time_dimension = None
        self.time_values = []  # List of time values
        self.current_time_index = 0
        self.band_dimension = None
        self.band_names = []  # List of band names
        self.current_band = 0
        self.dimensions_info = {}  # Full dimension information
        self.attributes = {}  # NetCDF global attributes
        self.coordinate_variables = []  # List of coordinate variables
        self.units = {}  # Units for each variable
        self.standard_names = {}  # Standard names for variables

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "variables": self.variables,
            "current_variable": self.current_variable,
            "time_dimension": self.time_dimension,
            "time_values": [str(t) for t in self.time_values],  # Convert to strings for JSON
            "current_time_index": self.current_time_index,
            "band_dimension": self.band_dimension,
            "band_names": self.band_names,
            "current_band": self.current_band,
            "dimensions_info": self.dimensions_info,
            "attributes": self.attributes,
            "coordinate_variables": self.coordinate_variables,
            "units": self.units,
            "standard_names": self.standard_names
        }

    def from_dict(self, data: Dict[str, Any]):
        """Load from dictionary."""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def get_time_range(self) -> tuple:
        """Get time range as (start, end) indices."""
        return (0, len(self.time_values) - 1) if self.time_values else (0, 0)

    def get_band_range(self) -> tuple:
        """Get band range as (start, end) indices."""
        return (0, len(self.band_names) - 1) if self.band_names else (0, 0)

    def get_current_time_value(self):
        """Get current time value."""
        if 0 <= self.current_time_index < len(self.time_values):
            return self.time_values[self.current_time_index]
        return None

    def get_current_band_name(self) -> str:
        """Get current band name."""
        if 0 <= self.current_band < len(self.band_names):
            return self.band_names[self.current_band]
        return f"Band {self.current_band + 1}"


def find_case_insensitive_key(names: List[str], *candidates: str) -> Optional[str]:
    """Return the first matching name using case-insensitive comparison."""
    lowered = {name.lower(): name for name in names}
    for candidate in candidates:
        match = lowered.get(candidate.lower())
        if match:
            return match
    return None

class LayerProperty:
    """Container for all layer properties."""

    def __init__(self):
        self.metadata = LayerMetadata()
        self.style = LayerStyleProperties()
        self.dimensions = LayerDimensions()
        self.netcdf = None  # NetCDFProperties, only for NetCDF layers
        self.custom_properties = {}  # Additional custom properties
        self.visible = True
        self._validation_errors = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = {
            "metadata": self.metadata.to_dict(),
            "style": self.style.to_dict(),
            "dimensions": self.dimensions.to_dict(),
            "netcdf": self.netcdf.to_dict() if self.netcdf else None,
            "custom_properties": self.custom_properties,
            "visible": self.visible
        }
        return data

    def from_dict(self, data: Dict[str, Any]):
        """Load from dictionary."""
        if "metadata" in data:
            self.metadata.from_dict(data["metadata"])
        if "style" in data:
            self.style.from_dict(data["style"])
        if "dimensions" in data:
            self.dimensions.from_dict(data["dimensions"])
        if "netcdf" in data and data["netcdf"]:
            if not self.netcdf:
                self.netcdf = NetCDFProperties()
            self.netcdf.from_dict(data["netcdf"])
        if "custom_properties" in data:
            self.custom_properties = data["custom_properties"]
        if "visible" in data:
            self.visible = data["visible"]

    def validate(self) -> List[str]:
        """Validate all properties and return list of errors."""
        errors = []

        # Validate metadata
        if not self.metadata.name:
            errors.append("Layer name is required")
        if not self.metadata.layer_type:
            errors.append("Layer type is required")

        # Validate style properties
        if not (0.0 <= self.style.transparency <= 1.0):
            errors.append("Transparency must be between 0.0 and 1.0")
        if self.style.line_width < 0:
            errors.append("Line width must be positive")
        if self.style.marker_size < 0:
            errors.append("Marker size must be positive")

        # Validate dimensions
        if len(self.dimensions.extent) == 4:
            minx, maxx, miny, maxy = self.dimensions.extent
            if minx >= maxx:
                errors.append("Invalid extent: minx must be less than maxx")
            if miny >= maxy:
                errors.append("Invalid extent: miny must be less than maxy")

        self._validation_errors = errors
        return errors

    def is_valid(self) -> bool:
        """Check if all properties are valid."""
        return len(self.validate()) == 0

    def get_validation_errors(self) -> List[str]:
        """Get current validation errors."""
        return self._validation_errors.copy()

# Property Manager
class LayerPropertyManager(QObject):
    """
    Manager for layer properties with signal-based updates.

    Signals:
        property_changed: Emitted when a property is changed (layer_name, property_path, value)
        layer_added: Emitted when a layer is added (layer_name)
        layer_removed: Emitted when a layer is removed (layer_name)
        validation_error: Emitted when validation fails (layer_name, errors)
    """

    property_changed = pyqtSignal(str, str, object)  # layer_name, property_path, value
    layer_added = pyqtSignal(str)  # layer_name
    layer_removed = pyqtSignal(str)  # layer_name
    validation_error = pyqtSignal(str, list)  # layer_name, errors

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layers = {}  # Dict[str, LayerProperty]
        self._property_locks = {}  # Dict[str, bool] for preventing recursive updates

    def add_layer(self, layer_name: str, layer_property: LayerProperty = None) -> LayerProperty:
        """Add a new layer property."""
        if layer_property is None:
            layer_property = LayerProperty()
            layer_property.metadata.name = layer_name

        # Validate before adding
        errors = layer_property.validate()
        if errors:
            self.validation_error.emit(layer_name, errors)

        self._layers[layer_name] = layer_property
        self._property_locks[layer_name] = False
        self.layer_added.emit(layer_name)
        return layer_property

    def remove_layer(self, layer_name: str) -> bool:
        """Remove a layer property."""
        if layer_name in self._layers:
            del self._layers[layer_name]
            if layer_name in self._property_locks:
                del self._property_locks[layer_name]
            self.layer_removed.emit(layer_name)
            return True
        return False

    def get_layer_property(self, layer_name: str) -> Optional[LayerProperty]:
        """Get layer property by name."""
        return self._layers.get(layer_name)

    def get_all_layers(self) -> Dict[str, LayerProperty]:
        """Get all layer properties."""
        return self._layers.copy()

    def get_layer_names(self) -> List[str]:
        """Get list of all layer names."""
        return list(self._layers.keys())

    def update_property(self, layer_name: str, property_path: str, value: Any) -> bool:
        """Update a specific property by path."""
        if layer_name not in self._layers:
            return False

        # Prevent recursive updates
        if self._property_locks.get(layer_name, False):
            return False

        try:
            self._property_locks[layer_name] = True
            layer_prop = self._layers[layer_name]

            # Parse property path
            path_parts = property_path.split('.')
            obj = layer_prop

            # Navigate to the parent object
            for part in path_parts[:-1]:
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                else:
                    return False

            # Set the final property
            final_property = path_parts[-1]
            if hasattr(obj, final_property):
                # Apply validation if available
                if hasattr(obj, f"validate_{final_property}"):
                    validator = getattr(obj, f"validate_{final_property}")
                    value = validator(value)

                setattr(obj, final_property, value)

                # Update modification date
                layer_prop.metadata.update_modification_date()

                # Validate after update
                errors = layer_prop.validate()
                if errors:
                    self.validation_error.emit(layer_name, errors)

                # Emit signal
                self.property_changed.emit(layer_name, property_path, value)
                return True

        except Exception as e:
            print(f"Error updating property {property_path}: {e}")
            return False
        finally:
            self._property_locks[layer_name] = False

        return False

    def get_property(self, layer_name: str, property_path: str) -> Any:
        """Get a specific property by path."""
        if layer_name not in self._layers:
            return None

        try:
            layer_prop = self._layers[layer_name]
            # Parse property path
            path_parts = property_path.split('.')
            obj = layer_prop

            # Navigate through the path
            for part in path_parts:
                if hasattr(obj, part):
                    obj = getattr(obj, part)
                else:
                    return None

            return obj

        except Exception as e:
            print(f"Error getting property {property_path}: {e}")
            return None

    def get_layer_info_summary(self, layer_name: str) -> Dict[str, Any]:
        """Get summary information about a layer."""
        if layer_name not in self._layers:
            return {}

        layer_prop = self._layers[layer_name]
        summary = {
            "name": layer_prop.metadata.name,
            "type": layer_prop.metadata.layer_type,
            "visible": layer_prop.visible,
            "transparency": f"{layer_prop.style.transparency:.1%}",
            "source": os.path.basename(layer_prop.metadata.source_file) if layer_prop.metadata.source_file else "Unknown",
            "dimensions": f"{layer_prop.dimensions.width} x {layer_prop.dimensions.height}" if layer_prop.dimensions.width else "Unknown",
            "crs": layer_prop.dimensions.crs or "Unknown",
            "extent": layer_prop.dimensions.extent,
            "area": f"{layer_prop.dimensions.get_area():.2f}" if layer_prop.dimensions.get_area() > 0 else "Unknown",
            "center": layer_prop.dimensions.get_center(),
            "file_size": f"{layer_prop.metadata.file_size / 1024 / 1024:.1f} MB" if layer_prop.metadata.file_size else "Unknown"
        }

        # Add NetCDF-specific info
        if layer_prop.netcdf:
            summary.update({
                "current_variable": layer_prop.netcdf.current_variable,
                "time_steps": len(layer_prop.netcdf.time_values),
                "current_time": layer_prop.netcdf.current_time_index,
                "bands": len(layer_prop.netcdf.band_names),
                "current_band": layer_prop.netcdf.current_band
            })

        return summary

    def save_properties_to_file(self, filepath: str) -> bool:
        """Save all properties to a JSON file."""
        try:
            data = {}
            for layer_name, layer_prop in self._layers.items():
                data[layer_name] = layer_prop.to_dict()

            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            return True

        except Exception as e:
            print(f"Error saving properties: {e}")
            return False

    def load_properties_from_file(self, filepath: str) -> bool:
        """Load properties from a JSON file."""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            for layer_name, layer_data in data.items():
                layer_prop = LayerProperty()
                layer_prop.from_dict(layer_data)
                self.add_layer(layer_name, layer_prop)

            return True

        except Exception as e:
            print(f"Error loading properties: {e}")
            return False

    def reset_all_properties(self):
        """Reset all properties and clear all layers."""
        layer_names = list(self._layers.keys())
        for layer_name in layer_names:
            self.remove_layer(layer_name)

    def duplicate_layer_properties(self, source_layer: str, target_layer: str) -> bool:
        """Duplicate properties from one layer to another."""
        if source_layer not in self._layers:
            return False

        source_prop = self._layers[source_layer]

        # Create a deep copy
        import copy
        new_prop = copy.deepcopy(source_prop)
        new_prop.metadata.name = target_layer
        new_prop.metadata.creation_date = datetime.datetime.now().isoformat()

        self.add_layer(target_layer, new_prop)
        return True

    def get_layers_by_type(self, layer_type: str) -> List[str]:
        """Get layer names filtered by type."""
        return [
            name for name, prop in self._layers.items()
            if prop.metadata.layer_type == layer_type
        ]

    def validate_all_layers(self) -> Dict[str, List[str]]:
        """Validate all layers and return errors."""
        validation_results = {}
        for layer_name, layer_prop in self._layers.items():
            errors = layer_prop.validate()
            if errors:
                validation_results[layer_name] = errors
        return validation_results

# UI Widgets
class ColorButton(QPushButton):
    """Custom button widget for color selection."""

    colorChanged = pyqtSignal(str)  # hex color

    def __init__(self, initial_color: str = "#FF0000", parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 25)
        self.color = initial_color
        self.update_color_display()
        self.clicked.connect(self.choose_color)

    def update_color_display(self):
        """Update button appearance to show current color."""
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.color};
                border: 2px solid #333;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                border: 2px solid #666;
            }}
        """)

    def choose_color(self):
        """Open color dialogs and update color."""
        color_dialog = QColorDialog(QColor(self.color), self)
        if color_dialog.exec() == QColorDialog.DialogCode.Accepted:
            new_color = color_dialog.selectedColor()
            self.set_color(new_color.name())

    def set_color(self, hex_color: str):
        """Set color programmatically."""
        self.color = hex_color
        self.update_color_display()
        self.colorChanged.emit(hex_color)

    def get_color(self) -> str:
        """Get current color."""
        return self.color

class PropertyTabWidget(QWidget):
    """Base class for property tab widgets."""

    property_changed = pyqtSignal(str, object)  # property_path, value

    def __init__(self, layer_property: LayerProperty, parent=None):
        super().__init__(parent)
        self.layer_property = layer_property
        self._updating = False
        self.setup_ui()
        self.load_properties()

    def setup_ui(self):
        """Setup the user interface. Override in subclasses."""
        pass

    def load_properties(self):
        """Load properties from layer property object. Override in subclasses."""
        pass

    def emit_property_changed(self, property_path: str, value: Any):
        """Emit property changed signal if not updating."""
        if not self._updating:
            self.property_changed.emit(property_path, value)

class MetadataTabWidget(PropertyTabWidget):
    """Tab widget for editing metadata properties."""

    def setup_ui(self):
        layout = QFormLayout(self)

        # Basic metadata
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(
            lambda text: self.emit_property_changed("metadata.name", text)
        )
        layout.addRow("Name:", self.name_edit)

        self.layer_type_combo = QComboBox()
        self.layer_type_combo.addItems(["vector", "raster", "netcdf", "unknown"])
        self.layer_type_combo.setEnabled(False)
        self.layer_type_combo.setToolTip("Detected layer type from the loaded file")
        layout.addRow("Detected Type:", self.layer_type_combo)

        self.source_file_edit = QLineEdit()
        self.source_file_edit.setReadOnly(True)
        layout.addRow("Source File:", self.source_file_edit)

        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(80)
        self.description_edit.textChanged.connect(
            lambda: self.emit_property_changed("metadata.description",
                                              self.description_edit.toPlainText())
        )
        layout.addRow("Description:", self.description_edit)

        # CRS information
        self.crs_edit = QLineEdit()
        self.crs_edit.textChanged.connect(
            lambda text: self.emit_property_changed("metadata.crs", text)
        )
        layout.addRow("CRS:", self.crs_edit)

        # Statistics (read-only)
        self.stats_group = QGroupBox("Statistics")
        stats_layout = QFormLayout(self.stats_group)

        self.min_label = QLabel("N/A")
        self.max_label = QLabel("N/A")
        self.mean_label = QLabel("N/A")
        self.std_label = QLabel("N/A")

        stats_layout.addRow("Minimum:", self.min_label)
        stats_layout.addRow("Maximum:", self.max_label)
        stats_layout.addRow("Mean:", self.mean_label)
        stats_layout.addRow("Std Dev:", self.std_label)

        layout.addRow(self.stats_group)

    def load_properties(self):
        self._updating = True
        metadata = self.layer_property.metadata

        self.name_edit.setText(metadata.name)
        self.layer_type_combo.setCurrentText(metadata.layer_type or "unknown")
        self.source_file_edit.setText(os.path.basename(metadata.source_file) if metadata.source_file else "")
        self.description_edit.setPlainText(metadata.description)
        self.crs_edit.setText(metadata.crs)

        # Update statistics
        stats = metadata.statistics
        if stats:
            self.min_label.setText(f"{stats.get('min', 'N/A')}")
            self.max_label.setText(f"{stats.get('max', 'N/A')}")
            self.mean_label.setText(f"{stats.get('mean', 'N/A'):.4f}" if 'mean' in stats else "N/A")
            self.std_label.setText(f"{stats.get('std', 'N/A'):.4f}" if 'std' in stats else "N/A")

        self._updating = False

class StyleTabWidget(PropertyTabWidget):
    """Tab widget for editing style properties."""

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # Common properties
        common_group = QGroupBox("Common Properties")
        common_layout = QFormLayout(common_group)

        # Visibility checkbox
        self.visible_check = QCheckBox()
        self.visible_check.toggled.connect(
            lambda checked: self.emit_property_changed("visible", checked)
        )
        common_layout.addRow("Visible:", self.visible_check)

        # Transparency slider
        transparency_layout = QHBoxLayout()
        self.transparency_slider = QSlider(Qt.Orientation.Horizontal)
        self.transparency_slider.setMinimum(0)
        self.transparency_slider.setMaximum(100)
        self.transparency_slider.valueChanged.connect(self._on_transparency_changed)

        self.transparency_spin = QSpinBox()
        self.transparency_spin.setMinimum(0)
        self.transparency_spin.setMaximum(100)
        self.transparency_spin.setSuffix("%")
        self.transparency_spin.valueChanged.connect(self._on_transparency_spin_changed)

        transparency_layout.addWidget(self.transparency_slider)
        transparency_layout.addWidget(self.transparency_spin)
        common_layout.addRow("Transparency:", transparency_layout)

        layout.addWidget(common_group)

        # Vector properties
        self.vector_group = QGroupBox("Vector Properties")
        vector_layout = QFormLayout(self.vector_group)

        # Colors
        self.color_button = ColorButton()
        self.color_button.colorChanged.connect(
            lambda color: self.emit_property_changed("style.color", color)
        )
        vector_layout.addRow("Color:", self.color_button)

        self.fill_color_button = ColorButton()
        self.fill_color_button.colorChanged.connect(
            lambda color: self.emit_property_changed("style.fill_color", color)
        )
        vector_layout.addRow("Fill Color:", self.fill_color_button)

        self.edge_color_button = ColorButton()
        self.edge_color_button.colorChanged.connect(
            lambda color: self.emit_property_changed("style.edge_color", color)
        )
        vector_layout.addRow("Edge Color:", self.edge_color_button)

        # Line properties
        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setMinimum(0.1)
        self.line_width_spin.setMaximum(10.0)
        self.line_width_spin.setSingleStep(0.1)
        self.line_width_spin.valueChanged.connect(
            lambda value: self.emit_property_changed("style.line_width", value)
        )
        vector_layout.addRow("Line Width:", self.line_width_spin)

        self.line_style_combo = QComboBox()
        self.line_style_combo.addItems(["solid", "dashed", "dotted", "dashdot"])
        self.line_style_combo.currentTextChanged.connect(
            lambda text: self.emit_property_changed("style.line_style", text)
        )
        vector_layout.addRow("Line Style:", self.line_style_combo)

        # Marker properties
        self.marker_style_combo = QComboBox()
        self.marker_style_combo.addItems(["circle", "square", "triangle", "diamond", "star"])
        self.marker_style_combo.currentTextChanged.connect(
            lambda text: self.emit_property_changed("style.marker_style", text)
        )
        vector_layout.addRow("Marker Style:", self.marker_style_combo)

        self.marker_size_spin = QDoubleSpinBox()
        self.marker_size_spin.setMinimum(0.1)
        self.marker_size_spin.setMaximum(20.0)
        self.marker_size_spin.setSingleStep(0.1)
        self.marker_size_spin.valueChanged.connect(
            lambda value: self.emit_property_changed("style.marker_size", value)
        )
        vector_layout.addRow("Marker Size:", self.marker_size_spin)

        layout.addWidget(self.vector_group)

        # Raster properties
        self.raster_group = QGroupBox("Raster Properties")
        raster_layout = QFormLayout(self.raster_group)

        # Colormap
        self.colormap_combo = QComboBox()
        # Add common colormaps
        colormaps = ["viridis", "plasma", "inferno", "magma", "cividis", "gray", "hot", "cool",
                    "spring", "summer", "autumn", "winter", "bone", "copper", "terrain"]
        self.colormap_combo.addItems(colormaps)
        self.colormap_combo.setEditable(True)
        self.colormap_combo.currentTextChanged.connect(
            lambda text: self.emit_property_changed("style.colormap", text)
        )
        raster_layout.addRow("Colormap:", self.colormap_combo)

        self.reverse_colormap_check = QCheckBox()
        self.reverse_colormap_check.toggled.connect(
            lambda checked: self.emit_property_changed("style.reverse_colormap", checked)
        )
        raster_layout.addRow("Reverse Colormap:", self.reverse_colormap_check)

        # Value range controls
        value_range_layout = QHBoxLayout()

        self.vmin_spin = QDoubleSpinBox()
        self.vmin_spin.setMinimum(-999999.0)
        self.vmin_spin.setMaximum(999999.0)
        self.vmin_spin.setDecimals(4)
        self.vmin_spin.setSpecialValueText("Auto")
        self.vmin_spin.setValue(self.vmin_spin.minimum())
        self.vmin_spin.valueChanged.connect(self._on_vmin_changed)

        self.vmax_spin = QDoubleSpinBox()
        self.vmax_spin.setMinimum(-999999.0)
        self.vmax_spin.setMaximum(999999.0)
        self.vmax_spin.setDecimals(4)
        self.vmax_spin.setSpecialValueText("Auto")
        self.vmax_spin.setValue(self.vmax_spin.maximum())
        self.vmax_spin.valueChanged.connect(self._on_vmax_changed)

        value_range_layout.addWidget(QLabel("Min:"))
        value_range_layout.addWidget(self.vmin_spin)
        value_range_layout.addWidget(QLabel("Max:"))
        value_range_layout.addWidget(self.vmax_spin)

        raster_layout.addRow("Value Range:", value_range_layout)

        # Interpolation method
        self.interpolation_combo = QComboBox()
        self.interpolation_combo.addItems(["nearest", "bilinear", "bicubic"])
        self.interpolation_combo.currentTextChanged.connect(
            lambda text: self.emit_property_changed("style.interpolation", text)
        )
        raster_layout.addRow("Interpolation:", self.interpolation_combo)

        layout.addWidget(self.raster_group)

        # Advanced properties
        self.advanced_group = QGroupBox("Advanced Properties")
        advanced_layout = QFormLayout(self.advanced_group)

        # Blend mode
        self.blend_mode_combo = QComboBox()
        self.blend_mode_combo.addItems(["normal", "multiply", "screen", "overlay", "darken", "lighten"])
        self.blend_mode_combo.currentTextChanged.connect(
            lambda text: self.emit_property_changed("style.blend_mode", text)
        )
        advanced_layout.addRow("Blend Mode:", self.blend_mode_combo)

        # Brightness, Contrast, Saturation sliders
        self.brightness_controls = self._create_adjustment_slider("brightness")
        advanced_layout.addRow("Brightness:", self.brightness_controls)

        self.contrast_controls = self._create_adjustment_slider("contrast")
        advanced_layout.addRow("Contrast:", self.contrast_controls)

        self.saturation_controls = self._create_adjustment_slider("saturation")
        advanced_layout.addRow("Saturation:", self.saturation_controls)

        # Hue shift
        hue_layout = QHBoxLayout()
        self.hue_shift_slider = QSlider(Qt.Orientation.Horizontal)
        self.hue_shift_slider.setMinimum(-180)
        self.hue_shift_slider.setMaximum(180)
        self.hue_shift_slider.setValue(0)
        self.hue_shift_slider.valueChanged.connect(self._on_hue_shift_changed)

        self.hue_shift_spin = QSpinBox()
        self.hue_shift_spin.setMinimum(-180)
        self.hue_shift_spin.setMaximum(180)
        self.hue_shift_spin.setSuffix("°")
        self.hue_shift_spin.valueChanged.connect(self._on_hue_shift_spin_changed)

        hue_layout.addWidget(self.hue_shift_slider)
        hue_layout.addWidget(self.hue_shift_spin)
        advanced_layout.addRow("Hue Shift:", hue_layout)

        layout.addWidget(self.advanced_group)

        self.apply_button = QPushButton("Apply Style Changes")
        self.apply_button.clicked.connect(self.apply_style_changes)
        layout.addWidget(self.apply_button)

        # Add stretch to push everything to the top
        layout.addStretch()

    def _create_adjustment_slider(self, property_name: str):
        """Create a slider for adjustment properties (-1.0 to 1.0)."""
        widget_layout = QHBoxLayout()

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(-100)
        slider.setMaximum(100)
        slider.setValue(0)

        spin = QSpinBox()
        spin.setMinimum(-100)
        spin.setMaximum(100)
        spin.setSuffix("%")

        # Connect signals
        slider.valueChanged.connect(
            lambda value: self._on_adjustment_changed(property_name, value, spin)
        )
        spin.valueChanged.connect(
            lambda value: self._on_adjustment_spin_changed(property_name, value, slider)
        )

        widget_layout.addWidget(slider)
        widget_layout.addWidget(spin)

        # Store references for later use
        setattr(self, f"{property_name}_slider", slider)
        setattr(self, f"{property_name}_spin", spin)

        container = QWidget()
        container.setLayout(widget_layout)
        return container

    def _on_transparency_changed(self, value: int):
        """Handle transparency slider change."""
        self.transparency_spin.blockSignals(True)
        self.transparency_spin.setValue(value)
        self.transparency_spin.blockSignals(False)

        # Convert percentage to 0.0-1.0 range
        transparency_value = value / 100.0
        self.emit_property_changed("style.transparency", transparency_value)

    def _on_transparency_spin_changed(self, value: int):
        """Handle transparency spinbox change."""
        self.transparency_slider.blockSignals(True)
        self.transparency_slider.setValue(value)
        self.transparency_slider.blockSignals(False)

        # Convert percentage to 0.0-1.0 range
        transparency_value = value / 100.0
        self.emit_property_changed("style.transparency", transparency_value)

    def _on_vmin_changed(self, value: float):
        """Handle vmin change."""
        if value == self.vmin_spin.minimum():
            self.emit_property_changed("style.vmin", None)
        else:
            self.emit_property_changed("style.vmin", value)

    def _on_vmax_changed(self, value: float):
        """Handle vmax change."""
        if value == self.vmax_spin.maximum():
            self.emit_property_changed("style.vmax", None)
        else:
            self.emit_property_changed("style.vmax", value)

    def _on_adjustment_changed(self, property_name: str, value: int, spin_widget):
        """Handle adjustment slider change."""
        spin_widget.blockSignals(True)
        spin_widget.setValue(value)
        spin_widget.blockSignals(False)

        # Convert percentage to -1.0 to 1.0 range
        adjusted_value = value / 100.0
        self.emit_property_changed(f"style.{property_name}", adjusted_value)

    def _on_adjustment_spin_changed(self, property_name: str, value: int, slider_widget):
        """Handle adjustment spinbox change."""
        slider_widget.blockSignals(True)
        slider_widget.setValue(value)
        slider_widget.blockSignals(False)

        # Convert percentage to -1.0 to 1.0 range
        adjusted_value = value / 100.0
        self.emit_property_changed(f"style.{property_name}", adjusted_value)

    def _on_hue_shift_changed(self, value: int):
        """Handle hue shift slider change."""
        self.hue_shift_spin.blockSignals(True)
        self.hue_shift_spin.setValue(value)
        self.hue_shift_spin.blockSignals(False)

        self.emit_property_changed("style.hue_shift", float(value))

    def _on_hue_shift_spin_changed(self, value: int):
        """Handle hue shift spinbox change."""
        self.hue_shift_slider.blockSignals(True)
        self.hue_shift_slider.setValue(value)
        self.hue_shift_slider.blockSignals(False)

        self.emit_property_changed("style.hue_shift", float(value))

    def load_properties(self):
        """Load properties from layer property object."""
        self._updating = True
        style = self.layer_property.style

        # Common properties
        self.visible_check.setChecked(self.layer_property.visible)

        # Transparency (convert from 0.0-1.0 to 0-100 percentage)
        transparency_percent = int(style.transparency * 100)
        self.transparency_slider.setValue(transparency_percent)
        self.transparency_spin.setValue(transparency_percent)

        # Vector properties
        self.color_button.set_color(style.color)
        self.fill_color_button.set_color(style.fill_color)
        self.edge_color_button.set_color(style.edge_color)
        self.line_width_spin.setValue(style.line_width)
        self.line_style_combo.setCurrentText(style.line_style)
        self.marker_style_combo.setCurrentText(style.marker_style)
        self.marker_size_spin.setValue(style.marker_size)

        # Raster properties
        self.colormap_combo.setCurrentText(style.colormap)
        self.reverse_colormap_check.setChecked(style.reverse_colormap)

        # Value range
        if style.vmin is not None:
            self.vmin_spin.setValue(style.vmin)
        else:
            self.vmin_spin.setValue(self.vmin_spin.minimum())

        if style.vmax is not None:
            self.vmax_spin.setValue(style.vmax)
        else:
            self.vmax_spin.setValue(self.vmax_spin.maximum())

        self.interpolation_combo.setCurrentText(style.interpolation)

        # Advanced properties
        self.blend_mode_combo.setCurrentText(style.blend_mode)

        # Adjustment properties (convert from -1.0 to 1.0 to -100 to 100 percentage)
        self.brightness_slider.setValue(int(style.brightness * 100))
        self.brightness_spin.setValue(int(style.brightness * 100))

        self.contrast_slider.setValue(int(style.contrast * 100))
        self.contrast_spin.setValue(int(style.contrast * 100))

        self.saturation_slider.setValue(int(style.saturation * 100))
        self.saturation_spin.setValue(int(style.saturation * 100))

        # Hue shift
        self.hue_shift_slider.setValue(int(style.hue_shift))
        self.hue_shift_spin.setValue(int(style.hue_shift))

        # Show/hide groups based on layer type
        layer_type = self.layer_property.metadata.layer_type
        self.vector_group.setVisible(layer_type == "vector")
        self.raster_group.setVisible(layer_type in ["raster", "netcdf"])

        self._updating = False

    def update_layer_type_visibility(self):
        """Update visibility of property groups based on layer type."""
        if hasattr(self, 'layer_property'):
            layer_type = self.layer_property.metadata.layer_type
            self.vector_group.setVisible(layer_type == "vector")
            self.raster_group.setVisible(layer_type in ["raster", "netcdf"])

    def apply_style_changes(self):
        """Explicitly re-emit the current style state to refresh the layer display."""
        style = self.layer_property.style
        self.emit_property_changed("visible", self.visible_check.isChecked())
        self.emit_property_changed("style.transparency", self.transparency_slider.value() / 100.0)
        self.emit_property_changed("style.color", self.color_button.get_color())
        self.emit_property_changed("style.fill_color", self.fill_color_button.get_color())
        self.emit_property_changed("style.edge_color", self.edge_color_button.get_color())
        self.emit_property_changed("style.line_width", self.line_width_spin.value())
        self.emit_property_changed("style.line_style", self.line_style_combo.currentText())
        self.emit_property_changed("style.marker_style", self.marker_style_combo.currentText())
        self.emit_property_changed("style.marker_size", self.marker_size_spin.value())
        self.emit_property_changed("style.colormap", self.colormap_combo.currentText())
        self.emit_property_changed("style.reverse_colormap", self.reverse_colormap_check.isChecked())
        self.emit_property_changed(
            "style.vmin",
            None if self.vmin_spin.value() == self.vmin_spin.minimum() else self.vmin_spin.value()
        )
        self.emit_property_changed(
            "style.vmax",
            None if self.vmax_spin.value() == self.vmax_spin.maximum() else self.vmax_spin.value()
        )
        self.emit_property_changed("style.interpolation", self.interpolation_combo.currentText())
        self.emit_property_changed("style.blend_mode", self.blend_mode_combo.currentText())
        self.emit_property_changed("style.brightness", self.brightness_slider.value() / 100.0)
        self.emit_property_changed("style.contrast", self.contrast_slider.value() / 100.0)
        self.emit_property_changed("style.saturation", self.saturation_slider.value() / 100.0)
        self.emit_property_changed("style.hue_shift", float(self.hue_shift_slider.value()))
        self.layer_property.style = style


class DimensionsTabWidget(PropertyTabWidget):
    """Read-only tab widget for layer dimensions and spatial extent."""

    def setup_ui(self):
        layout = QFormLayout(self)

        self.size_label = QLabel("N/A")
        self.extent_label = QLabel("N/A")
        self.center_label = QLabel("N/A")
        self.area_label = QLabel("N/A")
        self.pixel_size_label = QLabel("N/A")
        self.crs_label = QLabel("N/A")

        layout.addRow("Size:", self.size_label)
        layout.addRow("Extent:", self.extent_label)
        layout.addRow("Center:", self.center_label)
        layout.addRow("Area:", self.area_label)
        layout.addRow("Pixel Size:", self.pixel_size_label)
        layout.addRow("CRS:", self.crs_label)

    def load_properties(self):
        self._updating = True
        dimensions = self.layer_property.dimensions

        width = dimensions.width or 0
        height = dimensions.height or 0
        self.size_label.setText(f"{width} x {height}" if width or height else "Unknown")

        if len(dimensions.extent) == 4:
            minx, maxx, miny, maxy = dimensions.extent
            self.extent_label.setText(
                f"[{minx:.4f}, {maxx:.4f}, {miny:.4f}, {maxy:.4f}]"
            )
            center_x, center_y = dimensions.get_center()
            self.center_label.setText(f"({center_x:.4f}, {center_y:.4f})")
            self.area_label.setText(f"{dimensions.get_area():.4f}")
        else:
            self.extent_label.setText("Unknown")
            self.center_label.setText("Unknown")
            self.area_label.setText("Unknown")

        if dimensions.pixel_size_x or dimensions.pixel_size_y:
            self.pixel_size_label.setText(
                f"{dimensions.pixel_size_x:.6f}, {dimensions.pixel_size_y:.6f}"
            )
        else:
            self.pixel_size_label.setText("Unknown")

        self.crs_label.setText(dimensions.crs or self.layer_property.metadata.crs or "Unknown")
        self._updating = False


class NetCDFTabWidget(PropertyTabWidget):
    """Read-only tab widget for NetCDF-specific information."""

    def setup_ui(self):
        layout = QFormLayout(self)

        self.variable_label = QLabel("N/A")
        self.time_label = QLabel("N/A")
        self.band_label = QLabel("N/A")
        self.coord_label = QLabel("N/A")

        self.dimensions_text = QTextEdit()
        self.dimensions_text.setReadOnly(True)
        self.dimensions_text.setMaximumHeight(120)

        self.attributes_text = QTextEdit()
        self.attributes_text.setReadOnly(True)
        self.attributes_text.setMaximumHeight(150)

        layout.addRow("Variable:", self.variable_label)
        layout.addRow("Time:", self.time_label)
        layout.addRow("Band:", self.band_label)
        layout.addRow("Coordinates:", self.coord_label)
        layout.addRow("Dimensions:", self.dimensions_text)
        layout.addRow("Attributes:", self.attributes_text)

    def load_properties(self):
        self._updating = True
        netcdf = self.layer_property.netcdf

        if not netcdf:
            self.variable_label.setText("Not a NetCDF layer")
            self.time_label.setText("N/A")
            self.band_label.setText("N/A")
            self.coord_label.setText("N/A")
            self.dimensions_text.setPlainText("N/A")
            self.attributes_text.setPlainText("N/A")
            self._updating = False
            return

        self.variable_label.setText(netcdf.current_variable or ", ".join(netcdf.variables) or "N/A")

        if netcdf.time_values:
            current_time = netcdf.get_current_time_value()
            self.time_label.setText(
                f"{netcdf.time_dimension}: {netcdf.current_time_index + 1}/{len(netcdf.time_values)}"
                + (f" ({current_time})" if current_time is not None else "")
            )
        else:
            self.time_label.setText("No time dimension")

        self.band_label.setText(
            f"{netcdf.current_band + 1}/{len(netcdf.band_names)}"
            if netcdf.band_names else "Single band"
        )
        self.coord_label.setText(", ".join(netcdf.coordinate_variables) or "N/A")

        dimensions_info = netcdf.dimensions_info or {}
        self.dimensions_text.setPlainText(
            "\n".join(f"{name}: {size}" for name, size in dimensions_info.items()) or "N/A"
        )
        self.attributes_text.setPlainText(
            json.dumps(netcdf.attributes or {}, indent=2, default=str)
        )
        self._updating = False


class LayerPropertyEditor(QWidget):
    """Dock-friendly editor for inspecting and editing layer properties."""

    property_changed = pyqtSignal(str, object)

    def __init__(self, property_manager: LayerPropertyManager, parent=None):
        super().__init__(parent)
        self.property_manager = property_manager
        self.current_layer_name: Optional[str] = None
        self.metadata_tab: Optional[MetadataTabWidget] = None
        self.dimensions_tab: Optional[DimensionsTabWidget] = None
        self.netcdf_tab: Optional[NetCDFTabWidget] = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setMinimumSize(430, 560)

        self.layer_title = QLabel("No layer selected")
        font = self.layer_title.font()
        font.setBold(True)
        self.layer_title.setFont(font)
        layout.addWidget(self.layer_title)

        self.layer_summary = QLabel("Select a layer from the Layer Manager to inspect its properties.")
        self.layer_summary.setWordWrap(True)
        layout.addWidget(self.layer_summary)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.empty_label = QLabel("Properties will appear here when a layer is selected.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        self._set_editor_enabled(False)

    def _set_editor_enabled(self, enabled: bool):
        self.tabs.setVisible(enabled)
        self.empty_label.setVisible(not enabled)

    def _disconnect_tab_signals(self):
        for tab in (self.metadata_tab,):
            if tab is not None:
                try:
                    tab.property_changed.disconnect(self.property_changed)
                except TypeError:
                    pass

    def _build_tabs(self, layer_property: LayerProperty):
        self._disconnect_tab_signals()
        self.tabs.clear()

        self.metadata_tab = MetadataTabWidget(layer_property, self)
        self.dimensions_tab = DimensionsTabWidget(layer_property, self)
        self.netcdf_tab = NetCDFTabWidget(layer_property, self)

        self.metadata_tab.property_changed.connect(self.property_changed)

        self.tabs.addTab(self.metadata_tab, "Metadata")
        self.tabs.addTab(self.dimensions_tab, "Dimensions")

        if layer_property.netcdf or layer_property.metadata.layer_type == "netcdf":
            self.tabs.addTab(self.netcdf_tab, "NetCDF")

    def clear_editor(self):
        self.current_layer_name = None
        self.layer_title.setText("No layer selected")
        self.layer_summary.setText("Select a layer from the Layer Manager to inspect its properties.")
        self._disconnect_tab_signals()
        self.tabs.clear()
        self._set_editor_enabled(False)

    def refresh_current_layer(self):
        if self.current_layer_name:
            self.load_layer_properties(self.current_layer_name)

    def load_layer_properties(self, layer_name: str) -> bool:
        layer_property = self.property_manager.get_layer_property(layer_name)
        if not layer_property:
            self.clear_editor()
            return False

        self.current_layer_name = layer_name
        self.layer_title.setText(layer_property.metadata.name or layer_name)

        source_name = os.path.basename(layer_property.metadata.source_file) if layer_property.metadata.source_file else "Unknown source"
        self.layer_summary.setText(
            f"Type: {layer_property.metadata.layer_type or 'unknown'} | Source: {source_name}"
        )

        self._build_tabs(layer_property)
        self._set_editor_enabled(True)
        return True
