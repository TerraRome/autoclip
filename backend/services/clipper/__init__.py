"""Clipper engine, merged from Terra's Project Clipper (H:\\Project AI\\clipper\\viral).

- heatmap / signals / reasons / transcript: find viral moments in one YouTube video before
  downloading it ("Most replayed" heatmap, else live-chat spikes + viewer comment timestamps).
- finder: one call that returns ranked moments with a plain-language reason.
- streamer_layout: facecam/avatar on top, gameplay below, for gameplay streams and VTubers.

The modules keep Clipper's Indonesian comments and messages on purpose so the two projects
stay easy to diff.
"""
