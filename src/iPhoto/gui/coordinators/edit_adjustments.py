"""Adjustment parameter handlers for the edit view.

Every adjustment panel (crop, B&W, white balance, curve, levels, definition,
denoise, sharpen, vignette, selective colour) reports "previewed" and "committed"
through the same pair of handlers. They moved out of ``EditCoordinator``
verbatim; the shared value application still lives in the coordinator.
"""

from __future__ import annotations

import logging


from ...i18n import tr
from iPhoto.core.curve_resolver import DEFAULT_CURVE_POINTS
from iPhoto.core.levels_resolver import DEFAULT_LEVELS_HANDLES
from iPhoto.core.selective_color_resolver import DEFAULT_SELECTIVE_COLOR_RANGES

_LOGGER = logging.getLogger(__name__)


class EditAdjustmentMixin:
    """Preview/commit handlers for every adjustment panel."""

    def _handle_crop_changed(self, cx, cy, w, h):
        if self._session:
            self._session.set_values({
                "Crop_CX": float(cx), "Crop_CY": float(cy),
                "Crop_W": float(w), "Crop_H": float(h)
            }, emit_individual=False)

    def _handle_bw_params_previewed(self, params) -> None:
        """Apply transient Black & White previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "BW_Enabled": True,
                "BW_Intensity": float(params.intensity),
                "BW_Neutrals": float(params.neutrals),
                "BW_Tone": float(params.tone),
                "BW_Grain": float(params.grain),
                "BW_Master": float(params.master),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve BW preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_bw_params_committed(self, params) -> None:
        """Persist Black & White adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "BW_Enabled": True,
            "BW_Intensity": float(params.intensity),
            "BW_Neutrals": float(params.neutrals),
            "BW_Tone": float(params.tone),
            "BW_Grain": float(params.grain),
            "BW_Master": float(params.master),
        }
        self._session.set_values(updates)

    def _handle_wb_params_previewed(self, params) -> None:
        """Apply transient White Balance previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "WB_Enabled": True,
                "WB_Warmth": float(params.warmth),
                "WB_Temperature": float(params.temperature),
                "WB_Tint": float(params.tint),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve WB preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_wb_params_committed(self, params) -> None:
        """Persist White Balance adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "WB_Enabled": True,
            "WB_Warmth": float(params.warmth),
            "WB_Temperature": float(params.temperature),
            "WB_Tint": float(params.tint),
        }
        self._session.set_values(updates)

    def _handle_curve_params_previewed(self, curve_data: dict) -> None:
        """Apply transient curve previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Curve_Enabled": True,
                "Curve_RGB": curve_data.get("RGB", list(DEFAULT_CURVE_POINTS)),
                "Curve_Red": curve_data.get("Red", list(DEFAULT_CURVE_POINTS)),
                "Curve_Green": curve_data.get("Green", list(DEFAULT_CURVE_POINTS)),
                "Curve_Blue": curve_data.get("Blue", list(DEFAULT_CURVE_POINTS)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve curve preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_curve_params_committed(self, curve_data: dict) -> None:
        """Persist curve adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Curve_Enabled": True,
            "Curve_RGB": curve_data.get("RGB", list(DEFAULT_CURVE_POINTS)),
            "Curve_Red": curve_data.get("Red", list(DEFAULT_CURVE_POINTS)),
            "Curve_Green": curve_data.get("Green", list(DEFAULT_CURVE_POINTS)),
            "Curve_Blue": curve_data.get("Blue", list(DEFAULT_CURVE_POINTS)),
        }
        self._session.set_values(updates)

    def _handle_levels_params_previewed(self, levels_data: dict) -> None:
        """Apply transient levels previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Levels_Enabled": True,
                "Levels_Handles": levels_data.get("Handles", list(DEFAULT_LEVELS_HANDLES)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve levels preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_levels_params_committed(self, levels_data: dict) -> None:
        """Persist levels adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Levels_Enabled": True,
            "Levels_Handles": levels_data.get("Handles", list(DEFAULT_LEVELS_HANDLES)),
        }
        self._session.set_values(updates)

    def _handle_definition_params_previewed(self, def_data: dict) -> None:
        """Apply transient definition previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Definition_Enabled": True,
                "Definition_Value": float(def_data.get("Value", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve definition preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_definition_params_committed(self, def_data: dict) -> None:
        """Persist definition adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Definition_Enabled": True,
            "Definition_Value": float(def_data.get("Value", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_denoise_params_previewed(self, dn_data: dict) -> None:
        """Apply transient noise-reduction previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Denoise_Enabled": True,
                "Denoise_Amount": float(dn_data.get("Amount", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve denoise preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_denoise_params_committed(self, dn_data: dict) -> None:
        """Persist noise-reduction adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Denoise_Enabled": True,
            "Denoise_Amount": float(dn_data.get("Amount", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_sharpen_params_previewed(self, sh_data: dict) -> None:
        """Apply transient sharpen previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Sharpen_Enabled": True,
                "Sharpen_Intensity": float(sh_data.get("Intensity", 0.0)),
                "Sharpen_Edges": float(sh_data.get("Edges", 0.0)),
                "Sharpen_Falloff": float(sh_data.get("Falloff", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve sharpen preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_sharpen_params_committed(self, sh_data: dict) -> None:
        """Persist sharpen adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Sharpen_Enabled": True,
            "Sharpen_Intensity": float(sh_data.get("Intensity", 0.0)),
            "Sharpen_Edges": float(sh_data.get("Edges", 0.0)),
            "Sharpen_Falloff": float(sh_data.get("Falloff", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_vignette_params_previewed(self, vig_data: dict) -> None:
        """Apply transient vignette previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "Vignette_Enabled": True,
                "Vignette_Strength": float(vig_data.get("Strength", 0.0)),
                "Vignette_Radius": float(vig_data.get("Radius", 0.50)),
                "Vignette_Softness": float(vig_data.get("Softness", 0.0)),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve vignette preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_vignette_params_committed(self, vig_data: dict) -> None:
        """Persist vignette adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "Vignette_Enabled": True,
            "Vignette_Strength": float(vig_data.get("Strength", 0.0)),
            "Vignette_Radius": float(vig_data.get("Radius", 0.50)),
            "Vignette_Softness": float(vig_data.get("Softness", 0.0)),
        }
        self._session.set_values(updates)

    def _handle_selective_color_params_previewed(self, sc_data: dict) -> None:
        """Apply transient Selective Color previews without mutating session state."""

        if self._session is None or self._compare_active:
            return

        try:
            preview_values = self._session.values()
            preview_values.update({
                "SelectiveColor_Enabled": True,
                "SelectiveColor_Ranges": sc_data.get(
                    "Ranges",
                    [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
                ),
            })
            adjustments = self._resolve_adjustments_for_values(preview_values)
        except Exception:
            _LOGGER.exception("Failed to resolve selective color preview adjustments")
            return

        self._active_edit_viewport().set_adjustments(adjustments)

    def _handle_selective_color_params_committed(self, sc_data: dict) -> None:
        """Persist Selective Color adjustments into the active edit session."""

        if self._session is None:
            return

        updates = {
            "SelectiveColor_Enabled": True,
            "SelectiveColor_Ranges": sc_data.get(
                "Ranges",
                [list(r) for r in DEFAULT_SELECTIVE_COLOR_RANGES],
            ),
        }
        self._session.set_values(updates)

    def _handle_curve_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer."""

        active = mode is not None
        if active:
            self._eyedropper_target = "curve"
            # Deactivate the WB and Selective Color eyedroppers to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_wb_eyedropper()
            self._ui.edit_sidebar.deactivate_selective_color_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_wb_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer for WB."""

        active = mode is not None
        if active:
            self._eyedropper_target = "wb"
            # Deactivate the Curve eyedropper to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_curve_eyedropper()
            self._ui.edit_sidebar.deactivate_selective_color_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_selective_color_eyedropper_mode_changed(self, mode: object) -> None:
        """Toggle eyedropper sampling on the GL image viewer for Selective Color."""

        active = mode is not None
        if active:
            self._eyedropper_target = "selective_color"
            # Deactivate the other eyedroppers to enforce mutual exclusion.
            self._ui.edit_sidebar.deactivate_curve_eyedropper()
            self._ui.edit_sidebar.deactivate_wb_eyedropper()
        self._active_edit_viewport().set_eyedropper_mode(active)

    def _handle_color_picked(self, r: float, g: float, b: float) -> None:
        """Forward eyedropper color picks to the appropriate section."""

        if self._eyedropper_target == "wb":
            self._ui.edit_sidebar.handle_wb_color_picked(r, g, b)
        elif self._eyedropper_target == "selective_color":
            self._ui.edit_sidebar.handle_selective_color_color_picked(r, g, b)
        else:
            self._ui.edit_sidebar.handle_curve_color_picked(r, g, b)


__all__ = ["EditAdjustmentMixin"]
