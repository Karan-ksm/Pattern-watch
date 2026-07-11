"""Approximate bounding boxes for all 50 US states.

Each entry is (abbreviation, lamin, lomin, lamax, lomax) in degrees —
the lat/lon rectangle handed to the OpenSky API for the statewide view.
These are rough rectangles, not real borders: neighbouring states'
boxes overlap, and coastal boxes include some ocean. That's fine for
"show me the traffic over this state".

Note on Alaska: the Aleutian islands cross the antimeridian (180°),
which a single lamin/lomax box can't represent, so its box covers the
mainland only.
"""

US_STATE_BOUNDS = {
    "Alabama": ("AL", 30.2, -88.5, 35.0, -84.9),
    "Alaska": ("AK", 54.5, -168.0, 71.4, -130.0),
    "Arizona": ("AZ", 31.3, -114.8, 37.0, -109.0),
    "Arkansas": ("AR", 33.0, -94.6, 36.5, -89.6),
    "California": ("CA", 32.5, -124.4, 42.0, -114.1),
    "Colorado": ("CO", 37.0, -109.05, 41.0, -102.05),
    "Connecticut": ("CT", 40.98, -73.73, 42.05, -71.79),
    "Delaware": ("DE", 38.45, -75.79, 39.84, -75.05),
    "Florida": ("FL", 24.5, -87.6, 31.0, -80.0),
    "Georgia": ("GA", 30.36, -85.6, 35.0, -80.8),
    "Hawaii": ("HI", 18.9, -160.3, 22.25, -154.8),
    "Idaho": ("ID", 42.0, -117.24, 49.0, -111.04),
    "Illinois": ("IL", 36.97, -91.5, 42.5, -87.5),
    "Indiana": ("IN", 37.77, -88.1, 41.76, -84.78),
    "Iowa": ("IA", 40.37, -96.64, 43.5, -90.14),
    "Kansas": ("KS", 37.0, -102.05, 40.0, -94.6),
    "Kentucky": ("KY", 36.5, -89.6, 39.15, -81.96),
    "Louisiana": ("LA", 28.9, -94.05, 33.02, -88.8),
    "Maine": ("ME", 43.06, -71.08, 47.46, -66.95),
    "Maryland": ("MD", 37.9, -79.49, 39.72, -75.05),
    "Massachusetts": ("MA", 41.24, -73.51, 42.89, -69.93),
    "Michigan": ("MI", 41.7, -90.42, 48.3, -82.4),
    "Minnesota": ("MN", 43.5, -97.24, 49.38, -89.5),
    "Mississippi": ("MS", 30.17, -91.65, 35.0, -88.1),
    "Missouri": ("MO", 36.0, -95.77, 40.61, -89.1),
    "Montana": ("MT", 44.36, -116.05, 49.0, -104.04),
    "Nebraska": ("NE", 40.0, -104.05, 43.0, -95.3),
    "Nevada": ("NV", 35.0, -120.0, 42.0, -114.04),
    "New Hampshire": ("NH", 42.7, -72.56, 45.31, -70.7),
    "New Jersey": ("NJ", 38.93, -75.56, 41.36, -73.89),
    "New Mexico": ("NM", 31.33, -109.05, 37.0, -103.0),
    "New York": ("NY", 40.5, -79.76, 45.02, -71.86),
    "North Carolina": ("NC", 33.84, -84.32, 36.59, -75.46),
    "North Dakota": ("ND", 45.94, -104.05, 49.0, -96.55),
    "Ohio": ("OH", 38.4, -84.82, 41.98, -80.52),
    "Oklahoma": ("OK", 33.62, -103.0, 37.0, -94.43),
    "Oregon": ("OR", 41.99, -124.57, 46.29, -116.46),
    "Pennsylvania": ("PA", 39.72, -80.52, 42.27, -74.69),
    "Rhode Island": ("RI", 41.15, -71.86, 42.02, -71.12),
    "South Carolina": ("SC", 32.03, -83.35, 35.22, -78.54),
    "South Dakota": ("SD", 42.48, -104.06, 45.95, -96.44),
    "Tennessee": ("TN", 34.98, -90.31, 36.68, -81.65),
    "Texas": ("TX", 25.84, -106.65, 36.5, -93.51),
    "Utah": ("UT", 37.0, -114.05, 42.0, -109.04),
    "Vermont": ("VT", 42.73, -73.44, 45.02, -71.46),
    "Virginia": ("VA", 36.54, -83.68, 39.47, -75.24),
    "Washington": ("WA", 45.54, -124.85, 49.0, -116.92),
    "West Virginia": ("WV", 37.2, -82.64, 40.64, -77.72),
    "Wisconsin": ("WI", 42.49, -92.89, 47.31, -86.25),
    "Wyoming": ("WY", 41.0, -111.06, 45.0, -104.05),
}
