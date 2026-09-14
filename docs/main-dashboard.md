# MAIN dashboard and CAD assets

The MAIN view presents existing HTTP/SSE state only. Selecting a process or a 3D tag does not send hardware commands. HOME, START, PAUSE and STOP retain their existing routes. Active alarms and ACK/RESET are in LOG.

The renderer uses locally vendored Three.js 0.180.0 (MIT; web/vendor/three/LICENSE). No CDN or external model upload is required. It suspends rendering on other pages and only renders on camera changes, state changes or position interpolation.

## Model contract

web/assets/line-model.json identifies the GLB file, source label, P00–P06 anchors and named model nodes. A single node may belong to both P03 and P05 for the shared Dobot. Motions map node names to existing unit IDs and received mm or Joint values. Missing feedback holds the last position. P01 and P04 positions are completed-command positions, not continuous encoder feedback. State older than 16 seconds is visibly marked as stale.

The supplied 00_00_00_FINAL_ASSEMBLY.iam was converted locally through Autodesk Apprentice Server 2026: 341 component occurrences, 381 meshes, 158,964 triangles, 6,534,096-byte GLB. Surface tolerance is 0.035 cm. The GLB stores meters in Y-up coordinates; the renderer displays millimeters. Component transforms and part names are preserved, with neutral display colors replacing unsupported Inventor appearances.

The original assembly includes the frame, feed mechanisms, sensors and tower light. The Dobot, camera, press actuator, stand and output bin are supplemental display geometry, identified in the UI. Their placement and neutral pose are illustrative, not calibrated robot coordinates, encoder measurements, reachability or collision verification. Joint values drive the display arm; P03/P05 share one displayed robot. E3 is dimmed and held still while configured as excluded. Interior view makes cover panels translucent without changing the source model.

The display floor layout rotates the equipment group 90 degrees onto the floor while preserving local motion axes. The shared Dobot stays upright, with its displayed base lowered to 190 mm and its stand shortened accordingly; the output bin rests on the floor. These changes affect only 3D presentation.

The current process follows line.active_process during running, homing, pause and alarm states. Its MAIN card, 3D tag and equipment are amber; a selected process remains blue, and faults take red priority. Shared robot meshes retain the current process highlight when the other robot process is selected. Idle clears the current marker. Tag placement prioritizes the current process and avoids overlaps.

To regenerate the raw GLB on a Windows PC with Apprentice 2026 and pywin32 installed:

```powershell
python tools/export_apprentice_cad.py --project path/to/assembly.ipj --assembly path/to/assembly.iam --output path/to/export
```

The output includes cad-part-map.json for mapping parts to processes. Review web/assets/line-model.json and supplemental-equipment.js after any new assembly export. The exporter never saves changes to the Inventor source.

## Validation

Use tests/test_main_dashboard.cjs with REQUIRE_CAD=1 to require a real GLB load under the production Content-Security-Policy, test the five desktop sizes, camera aspect ratio, selection, LOG navigation, alarms, existing commands, and 3D page visibility. The normal mode validates layout while CAD conversion is pending. All test API responses are fixtures, and no hardware is contacted.
