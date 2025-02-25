import geopandas as gpd
import folium
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import time
import os
import subprocess
from datetime import datetime, timedelta
import re
import json
import webbrowser
from collections import defaultdict
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# 1. Load and Parse the KML File
def load_kml(kml_file_path, discussions=None):
    gdf = gpd.read_file(kml_file_path)
    gdf = gdf.to_crs(epsg=4326)  # WGS84
    kml_filename = os.path.basename(kml_file_path)
    if discussions and kml_filename in discussions:
        gdf['discussion'] = discussions[kml_filename]
    else:
        gdf['discussion'] = "No discussion available."
    #print(f"KML DataFrame columns for {kml_file_path}:", gdf.columns)  # Corrected from gmns to gdf.columns
    #print(f"First few rows of KML data for {kml_file_path}:", gdf.head())
    return gdf

# 2. Pre-process KML Data to Resolve Overlaps
def clean_kml_data(kml_data):
    risk_priority = {
        'Low risk': 6,
        'Slight risk': 5,
        'Enhanced risk': 4,
        'Moderate risk': 3,
        'High risk': 2,
    }
    kml_data['priority'] = kml_data['Name'].map(risk_priority).fillna(0)
    kml_data = kml_data.sort_values(by='priority', ascending=False)
    cleaned_gdf = gpd.GeoDataFrame(columns=kml_data.columns, crs=kml_data.crs)
    for idx, row in kml_data.iterrows():
        current_geom = row['geometry']
        for _, lower_row in cleaned_gdf.iterrows():
            if lower_row['priority'] < row['priority']:
                current_geom = current_geom.difference(lower_row['geometry'])
        if not current_geom.is_empty:
            new_row = gpd.GeoDataFrame([row.drop('geometry').to_dict() | {'geometry': current_geom}], crs=kml_data.crs)
            cleaned_gdf = pd.concat([cleaned_gdf, new_row], ignore_index=True)
    #print("Cleaned GeoDataFrame:", cleaned_gdf.head())
    return cleaned_gdf

# 3. Parse Time Range from KML Filename (Fixed versioning)
def parse_kml_time(kml_file):
    pattern = r"Convective Outlook(?:\s*(UPDAT(?:E|ED))(\d+)?)? (\d{8}) (\d{4}) - (\d{8}) (\d{4})(?:\s*\(\d+\))?\.kml"
    match = re.search(pattern, kml_file, re.IGNORECASE)
    if match:
        update_keyword, update_number, start_date, start_time, end_date, end_time = match.groups()
        start = datetime.strptime(f"{start_date} {start_time}", "%d%m%Y %H%M")
        end = datetime.strptime(f"{end_date} {end_time}", "%d%m%Y %H%M")
        if update_keyword is None:
            version = '1'
            #print(f"{kml_file}: No UPDATE keyword -> Version 1")
        elif update_number is None:
            version = '2'
            #print(f"{kml_file}: UPDATE/UPDATED found, no number -> Version 2")
        else:
            version = str(int(update_number) + 1)
            #print(f"{kml_file}: UPDATE{update_number} -> Version {version}")
        return start, end, version
    #print(f"Filename {kml_file} did not match the expected pattern.")
    return None, None, None

# 4. Load Discussions from Text File
def load_discussions(discussion_file_path):
    discussions = {}
    current_kml = None
    current_text = []
    
    with open(discussion_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('#'):
                continue
            if line.startswith('[') and line.endswith(']'):
                if current_kml and current_text:
                    discussions[current_kml] = '\n'.join(current_text).strip()
                current_kml = line[1:-1]
                current_text = []
            elif line and current_kml:
                current_text.append(line)
        if current_kml and current_text:
            discussions[current_kml] = '\n'.join(current_text).strip()
    
    #print("Loaded discussions for:", list(discussions.keys()))
    return discussions



# 5. Generate Discussion Template File (Append new KMLs without overwriting)
def generate_discussion_template(kml_files, discussion_file_path, root):
    existing_discussions = {}
    if os.path.exists(discussion_file_path):
        with open(discussion_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            sections = re.split(r'\n(?=\[.*\])\n?', content)
            for section in sections:
                if section.strip():
                    match = re.match(r'\[(.*?)\]', section)
                    if match:
                        kml_name = match.group(1)
                        existing_discussions[kml_name] = section.strip()
        #print(f"Existing discussions found for: {list(existing_discussions.keys())}")
    #else:
        #print(f"Discussion file '{discussion_file_path}' does not exist. Creating new file.")

    new_kmls = []
    for kml_path in kml_files:
        kml_filename = os.path.basename(kml_path)
        if kml_filename not in existing_discussions:
            start, end, version = parse_kml_time(kml_filename)
            if start and end:
                new_kmls.append((kml_filename, start, end, version))

    if new_kmls:
        prompt_for_discussions(new_kmls, discussion_file_path, root)
    elif not existing_discussions:
        with open(discussion_file_path, 'w', encoding='utf-8') as f:
            f.write("# Format: [KML_FILENAME]\n"
                    "# Discussion text goes here (can span multiple lines)\n"
                    "# End with a blank line to separate entries\n\n")
        #print(f"Created empty discussion template file: {discussion_file_path}")
    else:
        placeholder=0
        #print(f"No new KMLs to add to '{discussion_file_path}'")

def prompt_for_discussions(new_kmls, discussion_file_path, root):
    discussions = {}
    
    def submit_discussion(kml_filename, entry, window):
        discussion_text = entry.get("1.0", tk.END).strip()
        if discussion_text:
            discussions[kml_filename] = discussion_text
            window.destroy()
        else:
            messagebox.showwarning("Empty Discussion", "Please enter a discussion before submitting.")

    for kml_filename, start, end, version in new_kmls:
        window = tk.Toplevel(root)
        window.title(f"Discussion for {kml_filename}")
        window.geometry("400x300")
        
        label = ttk.Label(window, text=f"Enter discussion for {kml_filename}\n(Valid from {start.strftime('%d/%m/%Y %H:%M')} to {end.strftime('%d/%m/%Y %H:%M')}, Version {version}):")
        label.pack(pady=5)
        
        entry = scrolledtext.ScrolledText(window, width=40, height=10)
        entry.pack(pady=5)
        
        submit_btn = ttk.Button(window, text="Submit", command=lambda k=kml_filename, e=entry, w=window: submit_discussion(k, e, w))
        submit_btn.pack(pady=5)
        
        window.grab_set()  # Make the window modal
        root.wait_window(window)  # Wait for the window to close

    # After all discussions are entered, append to file
    if discussions:
        with open(discussion_file_path, 'a', encoding='utf-8') as f:
            if not os.path.exists(discussion_file_path) or os.stat(discussion_file_path).st_size == 0:
                f.write("# Format: [KML_FILENAME]\n"
                        "# Discussion text goes here (can span multiple lines)\n"
                        "# End with a blank line to separate entries\n\n")
            # Write discussions for only the new KMLs that have been entered
            current_time = datetime.now().strftime('%d/%m/%Y %H:%M')
            for kml_filename, start, end, version in new_kmls:
                if kml_filename in discussions:
                    f.write(f"[{kml_filename}]\n"
                            f"Convective discussion for {start.strftime('%d/%m/%Y %H:%M')} to {end.strftime('%d/%m/%Y %H:%M')}"
                            f"Version {version}\n"
                            f"Issued at {current_time}\n"
                            f"\n{discussions[kml_filename]}\n\n")
        #print(f"Appended {len(discussions)} new KML discussion entries to '{discussion_file_path}'")

# 6. Create Mapbox Map with Tooltips, Discussion, and Conditional Text Color
def create_mapbox_map(all_kmls_data, mapbox_access_token, uk_bounds, current_date):
    uk_min_lat, uk_max_lat = 47, 62
    uk_min_lon, uk_max_lon = -11, 3
    center_lat = 54.013176
    center_lon = 2.3252278
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=7,
        max_bounds=[[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]],
        tiles="https://api.mapbox.com/styles/v1/handry-outlook/cm6dn4z49008801s2f3qd29he/draft/tiles/{z}/{x}/{y}?access_token=" + mapbox_access_token,
        attr='Mapbox',
        zoom_control=False
    )
    risk_colors = {
        'Low risk': '#5aac91',
        'Slight risk': 'yellow',
        'Enhanced risk': 'orange',
        'Moderate risk': 'red',
        'High risk': 'purple',
    }

    default_kml = None
    max_version = -1
    for kml, (start, end, _, version) in all_kmls_data.items():
        if start <= current_date <= end:
            version_num = int(version)
            if version_num > max_version:
                max_version = version_num
                default_kml = kml

    default_layer_id = default_kml.split(os.sep)[-1].replace('.kml', '') if default_kml else 'none'

    layer_groups = {}
    layer_definitions = {}
    for kml, (_, _, kml_data, _) in all_kmls_data.items():
        layer_id = kml.split(os.sep)[-1].replace('.kml', '')
        layer_group = folium.FeatureGroup(name=layer_id, show=(kml == default_kml))
        folium.GeoJson(
            kml_data,
            style_function=lambda feature: {
                'fillColor': risk_colors.get(feature['properties']['Name'], 'none'),
                'color': risk_colors.get(feature['properties']['Name'], 'black'),
                'weight': 1 if feature['properties']['Name'] in risk_colors else 2,
                'fillOpacity': 0.3 if feature['properties']['Name'] in risk_colors else 0
            },
            tooltip=folium.GeoJsonTooltip(fields=['Name'], aliases=['Risk Level:'])
        ).add_to(layer_group)
        layer_group.add_to(m)
        layer_groups[layer_id] = layer_group
        layer_definitions[layer_id] = kml_data.to_json()

    bounds = [[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]]
    folium.FitBounds(bounds).add_to(m)

    # Add Handry Outlook icon as an ImageOverlay
    icon_url = "https://raw.githubusercontent.com/Handry-Outlook/Convective-Outlook/main/Handry_outlook_icon_pride_small.png"
    # Define bounds for the icon in the top-right corner
    # Adjust these bounds to position the icon (latitude/longitude coordinates)
    icon_bounds = [[uk_max_lat, uk_min_lon], [uk_max_lat - 2, uk_min_lon + 4]]
    icon_overlay = folium.raster_layers.ImageOverlay(
        name="Handry Outlook Icon",
        image=icon_url,
        bounds=icon_bounds,
        opacity=1.0,  # Fully opaque
        interactive=True,
        cross_origin=True,  # Allow cross-origin since it's a GitHub URL
        zindex=10000,  # High z-index to ensure it’s on top
    )
    icon_overlay.add_to(m)
    folium.Popup("Handry Outlook Icon").add_to(icon_overlay)  # Optional popup

    # Add LayerControl to toggle layers (including the icon)
    #folium.LayerControl().add_to(m)

    # Rest of your existing code (risk_priority, calendar_html, legend_html, etc.) remains unchanged
    risk_priority = {'High risk': 2, 'Moderate risk': 3, 'Enhanced risk': 4, 'Slight risk': 5, 'Low risk': 6}
    risk_colors_cal = {'High risk': 'purple', 'Moderate risk': 'red', 'Enhanced risk': 'orange', 'Slight risk': 'yellow', 'Low risk': '#5aac91'}
    date_risks = defaultdict(list)
    kml_times = {}
    for kml, (kml_start, kml_end, kml_gdf, version) in all_kmls_data.items():
        layer_id = kml.split(os.sep)[-1].replace('.kml', '')
        kml_times[layer_id] = f"Valid: {kml_start.strftime('%d/%m/%Y %H:%M')} to {kml_end.strftime('%d/%m/%Y %H:%M')}"
        current_date_iter = kml_start
        while current_date_iter <= kml_end:
            date_str = current_date_iter.strftime('%Y-%m-%d')
            version_num = int(version)
            base_date = kml_start.strftime('%d/%m/%Y')
            label = f"{base_date} Version {version_num}"
            unique_risks = set(row['Name'] for _, row in kml_gdf.iterrows())
            risks_for_day = [(risk, layer_id, label, version_num) for risk in unique_risks]
            date_risks[date_str].extend(risks_for_day)
            current_date_iter += timedelta(days=1)

    date_risks = dict(date_risks)
    months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    current_year = current_date.year
    years = list(range(current_year - 5, current_year + 6))
    month_options = "".join(f'<option value="{i+1}" {"selected" if i+1 == current_date.month else ""}>{month}</option>' for i, month in enumerate(months))
    year_options = "".join(f'<option value="{year}" {"selected" if year == current_year else ""}>{year}</option>' for year in years)

    month_start = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start.replace(month=month_start.month % 12 + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1))
    calendar_html = '<table id="calendarTable" style="border-collapse: collapse; width: 100%;"><tr><th colspan="7">' + month_start.strftime('%B %Y') + '</th></tr><tr>'
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
        calendar_html += f'<th style="font-size: 0.8em;">{day}</th>'
    calendar_html += '</tr><tr>'
    first_day = month_start.weekday()
    for _ in range(first_day):
        calendar_html += '<td></td>'
    current_day = month_start
    while current_day < next_month:
        date_str = current_day.strftime('%Y-%m-%d')
        risks = date_risks.get(date_str, [])
        if risks:
            primary_risks = [r for r, _, _, _ in risks if 'Risk of' not in r]
            highest_risk = min(primary_risks, key=lambda x: risk_priority.get(x, 10)) if primary_risks else None
            kml_id = risks[0][1] if risks else 'none'
            bg_color = risk_colors_cal.get(highest_risk, 'white') if highest_risk else 'white'
            has_risk_of = any('Risk of' in risk for risk, _, _, _ in risks)
            border_style = 'border: 2px solid black' if has_risk_of else 'border: 1px solid black'
            text_color = 'white' if bg_color in ['red', 'purple'] else 'black'
        else:
            bg_color = 'white'
            border_style = 'border: 1px solid black'
            kml_id = 'none'
            text_color = 'black'
        calendar_html += f'<td style="background-color: {bg_color}; {border_style}; text-align: center; cursor: pointer; color: {text_color}; font-size: 0.8em; padding: 0.2em;" onclick="selectDate(\'{date_str}\');">{current_day.day}</td>'
        if current_day.weekday() == 6:
            calendar_html += '</tr><tr>'
        current_day += timedelta(days=1)
    calendar_html += '</tr></table>'

    map_id = m.get_name()
    layer_groups_json = {k: v.get_name() for k, v in layer_groups.items()}
    legend_html = f'''
    <!-- Valid time in top-left corner -->
    <div id="validTime" style="position: fixed; top: 1vh; left: 1vw; background-color: rgba(255, 255, 255, 0.8); padding: 0.5em 1em; border: 1px solid white; border-radius: 3px; z-index: 10001; font-size: 1.2em;">
        {kml_times.get(default_layer_id, "No outlook has been issued for this day. Use risk calendar to see archive")}
    </div>
    <!-- Combined Legend Container -->
    <div id="legendContainer" style="position: fixed; bottom: 1vh; left: 0; width: 100%; background-color: white; border: 2px solid grey; z-index: 10000; font-size: 1em; padding: 0.5em; box-shadow: 0 -2px 6px rgba(0,0,0,0.3); box-sizing: border-box;">
        <!-- Weather Risk Legend (Always Visible) -->
        <div id="legendBox" style="position: relative; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
            <div style="display: flex; align-items: center; gap: 10px;">
                
                <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 5px;">
                    <div style="display: flex; align-items: center;"><i style="background: #5aac91; width: 1em; height: 1em; margin-right: 0.3em;"></i> Low risk</div>
                    <div style="display: flex; align-items: center;"><i style="background: yellow; width: 1em; height: 1em; margin-right: 0.3em;"></i> Slight risk</div>
                    <div style="display: flex; align-items: center;"><i style="background: orange; width: 1em; height: 1em; margin-right: 0.3em;"></i> Enhanced risk</div>
                    <div style="display: flex; align-items: center;"><i style="background: red; width: 1em; height: 1em; margin-right: 0.3em;"></i> Moderate risk</div>
                    <div style="display: flex; align-items: center;"><i style="background: purple; width: 1em; height: 1em; margin-right: 0.3em;"></i> High risk</div>
                    <div style="display: flex; align-items: center;"><i style="background: black; width: 1em; height: 1em; margin-right: 0.3em;"></i> Risk of severe thunderstorms</div>
                </div>
            </div>
            <select id="kmlDropdown" onchange="showLayer(this.value);" style="padding: 0.3em; font-size: 0.9em;"></select>
            <button id="toggleLegend" onclick="toggleLegend()" style="min-height: 40px; cursor: pointer; font-size: 1em; padding: 0.3em 0.6em; box-sizing: border-box;">▲</button>
        </div>
        <!-- Expandable Content (Hidden by Default) -->
        <div id="legendContent" style="display: none; margin-top: 0.5em; max-height: 70vh; overflow-y: auto;">
            <!-- Collapsible Convective Discussion -->
            <div style="margin-bottom: 0.5em;">
                <button onclick="toggleSection('discussionSection')" style="cursor: pointer; width: 100%; text-align: left; padding: 0.3em; font-size: 0.9em;">Convective Discussion ►</button>
                <div id="discussionSection" style="display: none;">
                    <div id="discussionText" style="max-height: 15vh; overflow-y: auto; border: 1px solid #ccc; padding: 0.5em; white-space: pre-wrap; font-size: 0.9em;">Select an outlook to view discussion.</div>
                </div>
            </div>
            <!-- Collapsible Risk Calendar -->
            <div>
                <button onclick="toggleSection('calendarSection')" style="cursor: pointer; width: 100%; text-align: left; padding: 0.3em; font-size: 0.9em;">Risk Calendar ►</button>
                <div id="calendarSection" style="display: none;">
                    <div style="display: flex; justify-content: space-between; width: 100%;">
                        <select id="monthDropdown" onchange="updateCalendar();" style="width: 48%; padding: 0.3em; font-size: 0.9em;">{month_options}</select>
                        <select id="yearDropdown" onchange="updateCalendar();" style="width: 48%; padding: 0.3em; font-size: 0.9em;">{year_options}</select>
                    </div>
                    <br>
                    <div id="calendarContainer" style="width: 100%;">{calendar_html}</div>
                    <!-- Chart Buttons -->
                    <div style="margin-top: 0.5em; display: flex; gap: 0.5em; flex-wrap: wrap; justify-content: center;">
                        <button onclick="window.open('monthly_charts.html', '_blank')" style="padding: 0.3em; font-size: 0.9em; cursor: pointer;">Monthly Outlook Charts</button>
                        <button onclick="window.open('yearly_charts.html', '_blank')" style="padding: 0.3em; font-size: 0.9em; cursor: pointer;">Yearly Outlook Charts</button>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- Responsive styles -->
    <style>
        body {{
            margin: 0;
            padding: 0;
            width: 100vw;
            height: 100vh;
            overflow-x: hidden;
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative;
        }}
        #legendContainer {{
            transition: all 0.3s ease;
        }}
        @media (max-width: 600px) {{ /* Mobile devices */
            #validTime {{
                font-size: 0.9em;
                padding: 0.3em 0.6em;
            }}
            #legendContainer {{
                font-size: 0.8em;
                padding: 0.3em;
            }}
            #legendBox {{
                min-height: 40px;
                gap: 5px;
            }}
            #legendBox select, #legendBox button {{
                font-size: 0.8em;
                padding: 0.2em 0.4em;
            }}
            #legendContent div, #legendContent button {{
                font-size: 0.8em;
            }}
            #calendarTable th, #calendarTable td {{
                font-size: 0.7em;
                padding: 0.1em;
            }}
            .leaflet-overlay-pane img {{ /* Ensure icon is visible on mobile */
                display: block !important;
            }}
        }}
        @media (min-width: 601px) {{ /* Non-mobile devices (tablets, desktops) */
            .leaflet-overlay-pane img {{ /* Hide icon on non-mobile */
                display: none !important;
            }}
        }}
        #map_{map_id} {{
            z-index: 1;
            width: 100%;
            height: 100%;
        }}
        .mapboxgl-control-container .mapboxgl-ctrl-zoom-in,
        .mapboxgl-control-container .mapboxgl-ctrl-zoom-out {{
            display: none !important;
        }}
    </style>
    <!-- Rest of the JavaScript remains unchanged -->
    <script>
    var layerGroups = {json.dumps(layer_groups_json, ensure_ascii=False)};
    var layerDefinitions = {json.dumps(layer_definitions, ensure_ascii=False)};
    var activeLayers = {{}};
    var dateRisks = {json.dumps(date_risks, ensure_ascii=False)};
    var riskColorsCal = {json.dumps(risk_colors_cal, ensure_ascii=False)};
    var defaultLayerId = '{default_layer_id}';
    var riskPriority = {json.dumps(risk_priority, ensure_ascii=False)};
    var kmlTimes = {json.dumps(kml_times, ensure_ascii=False)};

    function toggleSection(sectionId) {{
        var section = document.getElementById(sectionId);
        var button = section.previousElementSibling;
        if (section.style.display === 'none') {{
            section.style.display = 'block';
            button.innerHTML = button.innerHTML.replace('►', '▼');
            if (sectionId === 'calendarSection') {{
                updateCalendar();
            }}
        }} else {{
            section.style.display = 'none';
            button.innerHTML = button.innerHTML.replace('▼', '►');
        }}
    }}

    function showLayer(layerId) {{
        var map = {map_id};
        console.log('showLayer called with ID: ' + layerId);
        map.eachLayer(function(layer) {{
            if (layer instanceof L.GeoJSON || layer instanceof L.FeatureGroup) {{
                console.log('Removing layer with ID:', layer._leaflet_id);
                map.removeLayer(layer);
            }}
        }});
        activeLayers = {{}};
        if (layerId !== 'none' && layerDefinitions[layerId]) {{
            console.log('Adding layer:', layerId);
            var layer = L.geoJSON(JSON.parse(layerDefinitions[layerId]), {{
                style: function(feature) {{
                    var riskColors = {{
                        'Low risk': '#5aac91',
                        'Slight risk': 'yellow',
                        'Enhanced risk': 'orange',
                        'Moderate risk': 'red',
                        'High risk': 'purple'
                    }};
                    return {{
                        fillColor: riskColors[feature.properties.Name] || 'none',
                        color: riskColors[feature.properties.Name] || 'black',
                        weight: feature.properties.Name in riskColors ? 1 : 2,
                        fillOpacity: feature.properties.Name in riskColors ? 0.3 : 0
                    }};
                }},
                onEachFeature: function(feature, layer) {{
                    layer.bindTooltip(
                        '<b>Risk Level:</b> ' + feature.properties.Name,
                        {{sticky: true}}
                    );
                    layer.on('click', function() {{
                        document.getElementById('discussionText').innerHTML = feature.properties.discussion || 'No discussion available.';
                    }});
                }}
            }});
            layer.addTo(map);
            activeLayers[layerId] = layer;
            var firstFeature = JSON.parse(layerDefinitions[layerId]).features[0];
            document.getElementById('discussionText').innerHTML = firstFeature.properties.discussion || 'No discussion available.';
            document.getElementById('validTime').innerHTML = kmlTimes[layerId] || 'Valid time not available';
        }} else {{
            document.getElementById('discussionText').innerHTML = 'Select an outlook to view discussion.';
            document.getElementById('validTime').innerHTML = 'No convective outlook is available for this day, please select another day';
        }}
        document.getElementById('kmlDropdown').value = layerId;
    }}

    function updateCalendar() {{
        var monthDropdown = document.getElementById('monthDropdown');
        var yearDropdown = document.getElementById('yearDropdown');
        if (!monthDropdown || !yearDropdown) {{
            console.log('Dropdowns not found, skipping calendar update');
            return;
        }}
        var month = parseInt(monthDropdown.value);
        var year = parseInt(yearDropdown.value);
        console.log('Updating calendar for month:', month, 'year:', year);
        var monthStart = new Date(year, month - 1, 1);
        var nextMonth = new Date(year, month, 1);
        var firstDay = monthStart.getDay() === 0 ? 6 : monthStart.getDay() - 1;
        var currentDay = new Date(monthStart);
        var calendarHtml = '<table style="border-collapse: collapse; width: 100%;" id="calendarTable"><tr><th colspan="7">' + 
            monthStart.toLocaleString('default', {{month: 'long'}}) + ' ' + year + 
            '</th></tr><tr>';
        var days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        for (var day of days) {{
            calendarHtml += '<th style="font-size: 0.8em;">' + day + '</th>';
        }}
        calendarHtml += '</tr><tr>';
        for (var i = 0; i < firstDay; i++) {{
            calendarHtml += '<td></td>';
        }}
        while (currentDay < nextMonth) {{
            var dateStr = currentDay.toISOString().split('T')[0];
            var risks = dateRisks[dateStr] || [];
            var bgColor = 'white';
            var borderStyle = 'border: 1px solid black';
            var textColor = 'black';
            var kmlId = 'none';
            if (risks.length > 0) {{
                var primaryRisks = risks.filter(function(risk) {{return !risk[0].includes('Risk of');}});
                var highestRisk = primaryRisks.length > 0 ? 
                    primaryRisks.reduce(function(min, curr) {{ 
                        return (riskPriority[curr[0]] || 10) < (riskPriority[min[0]] || 10) ? curr : min; 
                    }})[0] : null;
                kmlId = risks[0][1];
                bgColor = highestRisk ? riskColorsCal[highestRisk] || 'white' : 'white';
                var hasRiskOf = risks.some(function(risk) {{return risk[0].includes('Risk of');}});
                borderStyle = hasRiskOf ? 'border: 2px solid black' : 'border: 1px solid black';
                textColor = (bgColor === 'red' || bgColor === 'purple') ? 'white' : 'black';
            }}
            calendarHtml += '<td style="background-color: ' + bgColor + '; ' + borderStyle + '; text-align: center; cursor: pointer; color: ' + textColor + '; font-size: 0.8em; padding: 0.2em;" ' +
                'onclick="selectDate(\\\'' + dateStr + '\\\');">' + currentDay.getDate() + '</td>';
            if (currentDay.getDay() === 0) {{
                calendarHtml += '</tr><tr>';
            }}
            currentDay.setDate(currentDay.getDate() + 1);
        }}
        calendarHtml += '</tr></table>';
        console.log('Replacing calendar HTML');
        var calendarContainer = document.getElementById('calendarContainer');
        if (calendarContainer) {{
            calendarContainer.innerHTML = calendarHtml;
        }} else {{
            console.log('Calendar container not found');
        }}
    }}

    function selectDate(dateStr) {{
        console.log('Date selected: ' + dateStr);
        var dropdown = document.getElementById('kmlDropdown');
        if (!dropdown) {{
            console.log('kmlDropdown not found');
            return;
        }}
        dropdown.innerHTML = '<option value="none">None</option>';
        var risks = dateRisks[dateStr] || [];
        var uniqueKmlsMap = new Map();
        risks.forEach(function(item) {{
            var risk = item[0];
            var kmlId = item[1];
            var label = item[2];
            var version = item[3];
            if (!uniqueKmlsMap.has(kmlId)) {{
                uniqueKmlsMap.set(kmlId, {{kmlId: kmlId, label: label, version: version}});
            }}
        }});
        var uniqueKmls = Array.from(uniqueKmlsMap.values()).sort(function(a, b) {{ 
            return b.version - a.version; 
        }});
        uniqueKmls.forEach(function(item) {{
            var option = document.createElement('option');
            option.value = item.kmlId;
            option.text = item.label;
            dropdown.appendChild(option);
        }});
        var defaultOption = (uniqueKmls.length > 0) ? uniqueKmls[0].kmlId : 'none';
        dropdown.value = defaultOption;
        showLayer(defaultOption);
    }}

    function toggleLegend() {{
        console.log('Toggling legend');
        var content = document.getElementById('legendContent');
        var button = document.getElementById('toggleLegend');
        if (content.style.display === 'none') {{
            content.style.display = 'block';
            button.innerHTML = '▼';
            updateCalendar();
        }} else {{
            content.style.display = 'none';
            button.innerHTML = '▲';
        }}
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        console.log('DOM loaded, initializing with default layer: ' + defaultLayerId);
        showLayer(defaultLayerId);
        updateCalendar();
        selectDate('{current_date.strftime('%Y-%m-%d')}');
        document.getElementById('monthDropdown').addEventListener('change', updateCalendar);
        document.getElementById('yearDropdown').addEventListener('change', updateCalendar);
    }});
    </script>
    '''
    
    m.get_root().html.add_child(folium.Element(legend_html))
    return m
  
def analyze_outlook_data(all_kmls_data):
    monthly_data = defaultdict(lambda: defaultdict(int))
    yearly_data = defaultdict(lambda: defaultdict(int))
    risk_levels = ['Low risk', 'Slight risk', 'Enhanced risk', 'Moderate risk', 'High risk']
    daily_latest_version = {}  # Track latest version per day

    # First pass: Identify the latest version per day
    for kml, (start, end, kml_data, version) in all_kmls_data.items():
        current_date = start
        while current_date <= end:
            date_str = current_date.strftime('%Y-%m-%d')
            version_num = int(version)
            if date_str not in daily_latest_version or version_num > daily_latest_version[date_str][1]:
                daily_latest_version[date_str] = (kml, version_num, kml_data)
            current_date += timedelta(days=1)

    # Second pass: Count unique risks only from the latest version per day
    for date_str, (_, _, kml_data) in daily_latest_version.items():
        year = int(date_str[:4])
        month = int(date_str[5:7])
        month_key = f"{year}-{month:02d}"
        # Use a set to ensure each risk level is counted only once per day (e.g., 3 "Slight risk" polygons = 1 count)
        unique_risks = set(row['Name'] for _, row in kml_data.iterrows() if row['Name'] in risk_levels)
        for risk in unique_risks:
            monthly_data[month_key][risk] += 1
            yearly_data[year][risk] += 1

    # Convert to JSON-friendly format
    monthly_json = {month: dict(risks) for month, risks in monthly_data.items()}
    yearly_json = {year: dict(risks) for year, risks in yearly_data.items()}
    
    return monthly_json, yearly_json

def create_monthly_chart_html(monthly_data, output_path):
    # Icon image URL (assuming it's in the repository root)
    github_repo_url = "https://raw.githubusercontent.com/Handry-Outlook/Convective-Outlook/main"
    icon_image_url = f"{github_repo_url}/Handry_outlook_icon_pride_small.png"
    
    html_content = f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Monthly Convective Outlook Charts</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 10px;
                display: flex;
                flex-direction: column;
                align-items: center;
                min-height: 100vh;
                overflow-x: hidden;
                position: relative; /* For positioning the icon */
            }}
            h1 {{
                font-size: 1.5em;
                margin-bottom: 10px;
                text-align: center;
            }}
            canvas {{
                width: 100% !important;
                max-width: 90vw;
                height: 90vh !important;
            }}
            /* Style for the icon */
            .handry-icon {{
                position: fixed;
                top: 10px;
                right: 10px;
                max-width: 50px; /* Reasonable size for desktop */
                height: auto;
                z-index: 10000; /* Ensure it’s above other elements */
            }}
            @media (max-width: 600px) {{
                h1 {{
                    font-size: 1.2em;
                }}
                canvas {{
                    max-width: 95vw;
                    height: 70vh !important;
                }}
                .handry-icon {{
                    max-width: 30px; /* Smaller size for mobile */
                    top: 5px;
                    right: 5px;
                }}
            }}
        </style>
    </head>
    <body>
        <h1>Monthly Convective Outlook Counts</h1>
        <img src="{icon_image_url}" alt="Handry Outlook Icon" class="handry-icon">
        <canvas id="monthlyChart"></canvas>
        <script>
            const monthlyData = {json.dumps(monthly_data)};
            const labels = Object.keys(monthlyData).sort();
            const datasets = [
                {{ label: 'Low risk', data: labels.map(month => monthlyData[month]['Low risk'] || 0), backgroundColor: '#5aac91' }},
                {{ label: 'Slight risk', data: labels.map(month => monthlyData[month]['Slight risk'] || 0), backgroundColor: 'yellow' }},
                {{ label: 'Enhanced risk', data: labels.map(month => monthlyData[month]['Enhanced risk'] || 0), backgroundColor: 'orange' }},
                {{ label: 'Moderate risk', data: labels.map(month => monthlyData[month]['Moderate risk'] || 0), backgroundColor: 'red' }},
                {{ label: 'High risk', data: labels.map(month => monthlyData[month]['High risk'] || 0), backgroundColor: 'purple' }}
            ];

            const ctx = document.getElementById('monthlyChart').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{ labels: labels, datasets: datasets }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{ stacked: false, title: {{ display: true, text: 'Month (YYYY-MM)', font: {{ size: 14 }} }}, ticks: {{ font: {{ size: 12 }} }} }},
                        y: {{ stacked: false, title: {{ display: true, text: 'Number of Days', font: {{ size: 14 }} }}, ticks: {{ font: {{ size: 12 }} }}, beginAtZero: true }}
                    }},
                    plugins: {{
                        legend: {{ position: 'top', labels: {{ font: {{ size: 12 }}, padding: 10, boxWidth: 20 }} }},
                        title: {{ display: true, text: 'Monthly Convective Outlooks by Risk Level', font: {{ size: 16 }} }}
                    }},
                    layout: {{ padding: {{ top: 10, bottom: 10, left: 10, right: 10 }} }}
                }}
            }});
        </script>
    </body>
    </html>
    '''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    #print(f"Monthly chart HTML saved as {output_path}")

def create_yearly_chart_html(yearly_data, output_path):
    # Icon image URL (assuming it's in the repository root)
    github_repo_url = "https://raw.githubusercontent.com/Handry-Outlook/Convective-Outlook/main"
    icon_image_url = f"{github_repo_url}/Handry_outlook_icon_pride_small.png"
    
    html_content = f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Yearly Convective Outlook Charts</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 10px;
                display: flex;
                flex-direction: column;
                align-items: center;
                min-height: 100vh;
                overflow-x: hidden;
                position: relative; /* For positioning the icon */
            }}
            h1 {{
                font-size: 1.5em;
                margin-bottom: 10px;
                text-align: center;
            }}
            canvas {{
                width: 100% !important;
                max-width: 90vw;
                height: 100vh !important;
            }}
            /* Style for the icon */
            .handry-icon {{
                position: fixed;
                top: 10px;
                right: 10px;
                max-width: 50px; /* Reasonable size for desktop */
                height: auto;
                z-index: 10000; /* Ensure it’s above other elements */
            }}
            @media (max-width: 600px) {{
                h1 {{
                    font-size: 1.2em;
                }}
                canvas {{
                    max-width: 95vw;
                    height: 70vh !important;
                }}
                .handry-icon {{
                    max-width: 30px; /* Smaller size for mobile */
                    top: 5px;
                    right: 5px;
                }}
            }}
        </style>
    </head>
    <body>
        <h1>Yearly Convective Outlook Counts</h1>
        <img src="{icon_image_url}" alt="Handry Outlook Icon" class="handry-icon">
        <canvas id="yearlyChart"></canvas>
        <script>
            const yearlyData = {json.dumps(yearly_data)};
            const labels = Object.keys(yearlyData).sort();
            const datasets = [
                {{ label: 'Low risk', data: labels.map(year => yearlyData[year]['Low risk'] || 0), backgroundColor: '#5aac91' }},
                {{ label: 'Slight risk', data: labels.map(year => yearlyData[year]['Slight risk'] || 0), backgroundColor: 'yellow' }},
                {{ label: 'Enhanced risk', data: labels.map(year => yearlyData[year]['Enhanced risk'] || 0), backgroundColor: 'orange' }},
                {{ label: 'Moderate risk', data: labels.map(year => yearlyData[year]['Moderate risk'] || 0), backgroundColor: 'red' }},
                {{ label: 'High risk', data: labels.map(year => yearlyData[year]['High risk'] || 0), backgroundColor: 'purple' }}
            ];

            const ctx = document.getElementById('yearlyChart').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{ labels: labels, datasets: datasets }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{ stacked: false, title: {{ display: true, text: 'Year', font: {{ size: 14 }} }}, ticks: {{ font: {{ size: 12 }} }} }},
                        y: {{ stacked: false, title: {{ display: true, text: 'Number of Days', font: {{ size: 14 }} }}, ticks: {{ font: {{ size: 12 }} }}, beginAtZero: true }}
                    }},
                    plugins: {{
                        legend: {{ position: 'top', labels: {{ font: {{ size: 12 }}, padding: 10, boxWidth: 20 }} }},
                        title: {{ display: true, text: 'Yearly Convective Outlooks by Risk Level', font: {{ size: 16 }} }}
                    }},
                    layout: {{ padding: {{ top: 10, bottom: 10, left: 10, right: 10 }} }}
                }}
            }});
        </script>
    </body>
    </html>
    '''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    #print(f"Yearly chart HTML saved as {output_path}")
    
# 7. Export Map as Image using Selenium
def export_map_image(map_object, output_path, width=1360, height=1760):
    map_object.save("temp_map.html")
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(f'--window-size={width},{height}')
    driver = webdriver.Chrome(options=chrome_options, service=webdriver.chrome.service.Service(ChromeDriverManager().install()))
    driver.get(f"file://{os.path.abspath('temp_map.html')}")
    time.sleep(10)
    driver.save_screenshot(output_path)
    driver.quit()

# 8. Overlay Template on Map
def overlay_on_template(map_image_path, template_image_path, output_path, position=(0, 0)):
    map_img = Image.open(map_image_path).convert('RGBA')
    template = Image.open(template_image_path).convert('RGBA')
    map_img.paste(template, position, template)
    map_img.save(output_path, 'PNG', quality=100, optimize=False)

def save_interactive_map(map_object, html_output_path, preview_image_name="map_preview.png", 
                        github_repo_url="https://raw.githubusercontent.com/Handry-Outlook/Convective-Outlook/main"):
    map_object.save(html_output_path)
    with open(html_output_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # Ensure the preview image is in the repository's root or a subfolder
    preview_image_path = os.path.join(os.path.dirname(html_output_path), preview_image_name)
    
    # Construct the OG image URL using the raw GitHub content URL
    # Use raw.githubusercontent.com for direct file access
    og_image_url = f"{github_repo_url}/{preview_image_name}"
    
    # Icon image URL (assuming it's in the repository root)
    icon_image_url = f"{github_repo_url}/Handry_outlook_icon_pride_small.png"
    
    meta_tags = f'''
    <meta property="og:title" content="Handry Outlook's Convective Outlooks">
    <meta property="og:description" content="Interactive map showing thunderstorms risks across the UK.">
    <meta property="og:image" content="{og_image_url}">
    <meta property="og:url" content="https://Handry-Outlook.github.io/Convective-Outlook/{html_output_path}">
    <meta property="og:type" content="website">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <style>
        body {{
            margin: 0;
            padding: 0;
            width: 100vw;
            height: 100vh;
            overflow-x: hidden;
            display: flex;
            justify-content: center;
            align-items: center;
            position: relative; /* For positioning the icon */
        }}
        #map_{map_object.get_name()} {{
            width: 100%;
            height: 100%;
        }}
        /* Style for the icon */
        .handry-icon {{
            position: fixed;
            top: 10px;
            right: 10px;
            max-width: 150px; /* Reasonable size for desktop */
            height: auto;
            z-index: 10000; /* Ensure it’s above other elements */
        }}
        @media (max-width: 600px) {{
            .handry-icon {{
                max-width: 0px; /* Smaller size for mobile */
                top: 5px;
                right: 5px;
            }}
        }}
        /* Hide Mapbox zoom controls */
        .mapboxgl-control-container .mapboxgl-ctrl-zoom-in,
        .mapboxgl-control-container .mapboxgl-ctrl-zoom-out {{
            display: none !important;
        }}
    </style>
    '''
    # Add the icon image to the body
    icon_html = f'<img src="{icon_image_url}" alt="Handry Outlook Icon" class="handry-icon">'
    updated_html = html_content.replace('<body>', f'<body>\n{icon_html}')
    updated_html = updated_html.replace('<head>', f'<head>\n{meta_tags}')
    
    with open(html_output_path, 'w', encoding='utf-8') as f:
        f.write(updated_html)
    
    # Verify the preview image exists and is committed to GitHub
    #if not os.path.exists(preview_image_path):
        #print(f"Warning: Preview image '{preview_image_path}' does not exist locally. Ensure it’s generated and committed.")
    #else:
        #print(f"Preview image found at {preview_image_path}")
    
    # Verify the icon image exists
    icon_local_path = os.path.join(os.path.dirname(html_output_path), "Handry_outlook_icon_pride_small.png")
    #if not os.path.exists(icon_local_path):
        #print(f"Warning: Icon image '{icon_local_path}' does not exist locally. Ensure it’s in the repository and committed.")
    #else:
        #print(f"Icon image found at {icon_local_path}")
    
    #print(f"Interactive map saved as {html_output_path} with Open Graph, viewport, and CSS")
    return html_output_path

def run_processing(root, status_label):
    status_label.config(text="Loading... Please wait.")
    root.update_idletasks()  # Update UI to show loading message

    template_img = "2024 convective outlook FINALISED 2.png"
    mapbox_access_token = "pk.eyJ1IjoiaGFuZHJ5LW91dGxvb2siLCJhIjoiY2xrbnNrbmVlMXo0NDNqa2d3MTY2NW90bCJ9.AJbccNwtKvKA8il3JkE3PA"
    uk_bounds = {'min_lat': 47.6, 'max_lat': 62.1, 'min_lon': -13.7, 'max_lon': 7.4}
    output_map_img = "temp_map.png"
    final_output = "final_output.png"
    interactive_map_html = "interactive_map.html"
    discussion_file = "convective_discussions.txt"
    preview_image = "map_preview.png"

    current_date = datetime.now()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    uk_weather_dir = os.path.dirname(script_dir)

    #print(f"Scanning UK Weather directory and subdirectories ({uk_weather_dir}) for 2022-2025:")
    all_files = []
    for root_dir, dirs, files in os.walk(uk_weather_dir):
        rel_root = os.path.relpath(root_dir, uk_weather_dir)
        if rel_root == '.' or re.match(r'202[2-5](?:\\.*)?$', rel_root):
            for f in files:
                full_path = os.path.join(root_dir, f)
                rel_path = os.path.relpath(full_path, uk_weather_dir)
                all_files.append(rel_path)
                #print(f" - {rel_path}")

    kml_files = []
    for root_dir, _, files in os.walk(uk_weather_dir):
        rel_root = os.path.relpath(root_dir, uk_weather_dir)
        if rel_root == '.' or re.match(r'202[2-5](?:\\.*)?$', rel_root):
            for f in files:
                if re.match(r"Convective Outlook(?: UPDAT(?:E|ED)(\d+)?)? \d{8} \d{4} - \d{8} \d{4}(?:\s*\(\d+\))?\.kml", f, flags=re.IGNORECASE):
                    kml_files.append(os.path.join(root_dir, f))

    if not kml_files:
        #print("No KML files found matching the pattern in UK Weather/202[2-5] directories!")
        messagebox.showerror("Error", "No KML files found!")
        status_label.config(text="Error: No KML files found.")
        return

    generate_discussion_template(kml_files, discussion_file, root)
    discussions = load_discussions(discussion_file) if os.path.exists(discussion_file) else {}

    all_kmls_data_raw = {}
    for kml_path in kml_files:
        kml = os.path.relpath(kml_path, uk_weather_dir)
        start, end, version = parse_kml_time(os.path.basename(kml_path))
        if start and end:
            kml_data = load_kml(kml_path, discussions)
            cleaned_data = clean_kml_data(kml_data)
            all_kmls_data_raw[kml] = (start, end, cleaned_data, version)

    all_kmls_data = {kml: all_kmls_data_raw[kml] for kml in all_kmls_data_raw}

    monthly_data, yearly_data = analyze_outlook_data(all_kmls_data)
    create_monthly_chart_html(monthly_data, "monthly_charts.html")
    create_yearly_chart_html(yearly_data, "yearly_charts.html")

    #("Detected Convective Outlooks (KML files):")
    date_version_counts = defaultdict(int)
    for i, (kml, (start, end, data, version)) in enumerate(all_kmls_data.items(), 1):
        base_date = start.strftime('%d/%m/%Y')
        version_num = int(version)
        date_version_counts[base_date] = max(date_version_counts[base_date], version_num)
        version_label = f"{base_date} Version {version_num}"
        #print(f"{i}. {kml} -> {version_label}")

    map_obj = create_mapbox_map(all_kmls_data, mapbox_access_token, uk_bounds, current_date)
    export_map_image(map_obj, output_map_img)
    overlay_on_template(output_map_img, template_img, final_output, position=(3236, 0))
    save_interactive_map(map_obj, interactive_map_html, preview_image_name=preview_image)

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    github_url = f"https://Handry-Outlook.github.io/Convective-Outlook/{interactive_map_html}"
    try:
        os.chdir(repo_dir)
        subprocess.run(["git", "add", interactive_map_html], check=True)
        subprocess.run(["git", "add", final_output], check=True)
        subprocess.run(["git", "add", preview_image], check=True)
        subprocess.run(["git", "add", "monthly_charts.html"], check=True)
        subprocess.run(["git", "add", "yearly_charts.html"], check=True)
        subprocess.run(["git", "commit", "-m", "Update interactive map with charts and preview image"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        #print("Interactive map, charts, and preview image successfully pushed to GitHub!")
        webbrowser.open(github_url)
        status_label.config(text="Processing complete! Map and charts uploaded.")
    except subprocess.CalledProcessError as e:
        #print(f"Git operation failed: {e}")
        #print("Ensure Git is set up correctly and your PAT is valid.")
        #print(f"URL not opened: {github_url}")
        status_label.config(text="Git operation failed. Check console.")
    except Exception as e:
        #print(f"Failed to open browser: {e}")
        #print(f"URL not opened: {github_url}")
        status_label.config(text="Error occurred. Check console.")

    #print(f"Final image saved as {final_output}")

def main():
    root = tk.Tk()
    root.title("Convective Outlook Processor")
    root.geometry("300x150")
    
    label = ttk.Label(root, text="Click 'Run' to process new convective outlooks.")
    label.pack(pady=10)
    
    status_label = ttk.Label(root, text="")
    status_label.pack(pady=5)
    
    run_btn = ttk.Button(root, text="Run", command=lambda: run_processing(root, status_label))
    run_btn.pack(pady=10)
    
    root.mainloop()

if __name__ == "__main__":
    main()