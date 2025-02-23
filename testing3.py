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

# 1. Load and Parse the KML File
def load_kml(kml_file_path, discussions=None):
    gdf = gpd.read_file(kml_file_path)
    gdf = gdf.to_crs(epsg=4326)  # WGS84
    kml_filename = os.path.basename(kml_file_path)
    if discussions and kml_filename in discussions:
        gdf['discussion'] = discussions[kml_filename]
    else:
        gdf['discussion'] = "No discussion available."
    print(f"KML DataFrame columns for {kml_file_path}:", gdf.columns)
    print(f"First few rows of KML data for {kml_file_path}:", gdf.head())
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
    print("Cleaned GeoDataFrame:", cleaned_gdf.head())
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
            print(f"{kml_file}: No UPDATE keyword -> Version 1")
        elif update_number is None:
            version = '2'
            print(f"{kml_file}: UPDATE/UPDATED found, no number -> Version 2")
        else:
            version = str(int(update_number) + 1)
            print(f"{kml_file}: UPDATE{update_number} -> Version {version}")
        return start, end, version
    print(f"Filename {kml_file} did not match the expected pattern.")
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
    
    print("Loaded discussions for:", list(discussions.keys()))
    return discussions

# 5. Generate Discussion Template File (Append new KMLs without overwriting)
def generate_discussion_template(kml_files, discussion_file_path):
    # Load existing discussions to avoid duplicating entries
    existing_discussions = {}
    if os.path.exists(discussion_file_path):
        with open(discussion_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Split content into sections based on KML headers ([filename])
            sections = re.split(r'\n(?=\[.*\])\n?', content)
            for section in sections:
                if section.strip():
                    match = re.match(r'\[(.*?)\]', section)
                    if match:
                        kml_name = match.group(1)
                        existing_discussions[kml_name] = section.strip()
        print(f"Existing discussions found for: {list(existing_discussions.keys())}")
    else:
        print(f"Discussion file '{discussion_file_path}' does not exist. Creating new file.")

    # Append new KML entries if they don't already exist
    new_entries = []
    for kml_path in kml_files:
        kml_filename = os.path.basename(kml_path)
        if kml_filename not in existing_discussions:
            start, end, version = parse_kml_time(kml_filename)
            if start and end:
                entry = (f"[{kml_filename}]\n"
                         f"Convective discussion for {kml_filename} (Valid from {start.strftime('%d/%m/%Y %H:%M')} to {end.strftime('%d/%m/%Y %H:%M')}, Version {version}):\n"
                         f"Add your discussion here.\n\n")
                new_entries.append(entry)
    
    if new_entries:
        with open(discussion_file_path, 'a', encoding='utf-8') as f:
            if not existing_discussions:
                f.write("# Format: [KML_FILENAME]\n"
                        "# Discussion text goes here (can span multiple lines)\n"
                        "# End with a blank line to separate entries\n\n")
            f.writelines(new_entries)
        print(f"Appended {len(new_entries)} new KML discussion entries to '{discussion_file_path}'")
    elif not existing_discussions:
        with open(discussion_file_path, 'w', encoding='utf-8') as f:
            f.write("# Format: [KML_FILENAME]\n"
                    "# Discussion text goes here (can span multiple lines)\n"
                    "# End with a blank line to separate entries\n\n")
        print(f"Created empty discussion template file: {discussion_file_path}")
    else:
        print(f"No new KMLs to add to '{discussion_file_path}'")

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
        attr='Mapbox'
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

    risk_priority = {'High risk': 2, 'Moderate risk': 3, 'Enhanced risk': 4, 'Slight risk': 5, 'Low risk': 6}
    risk_colors_cal = {'High risk': 'purple', 'Moderate risk': 'red', 'Enhanced risk': 'orange', 'Slight risk': 'yellow', 'Low risk': '#5aac91'}
    date_risks = {}
    for kml, (kml_start, kml_end, kml_gdf, version) in all_kmls_data.items():
        current_date_iter = kml_start
        while current_date_iter <= kml_end:
            date_str = current_date_iter.strftime('%Y-%m-%d')
            if date_str not in date_risks:
                date_risks[date_str] = []
            layer_id = kml.split(os.sep)[-1].replace('.kml', '')
            base_date = kml_start.strftime('%d/%m/%Y')
            version_num = int(version)
            label = f"{base_date} Version {version_num}"
            for _, row in kml_gdf.iterrows():
                risk = row['Name']
                date_risks[date_str].append((risk, layer_id, label, version_num))
            current_date_iter += timedelta(days=1)

    months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    current_year = current_date.year
    years = list(range(current_year - 5, current_year + 6))
    month_options = "".join(f'<option value="{i+1}" {"selected" if i+1 == current_date.month else ""}>{month}</option>' for i, month in enumerate(months))
    year_options = "".join(f'<option value="{year}" {"selected" if year == current_year else ""}>{year}</option>' for year in years)

    month_start = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (month_start.replace(month=month_start.month % 12 + 1) if month_start.month < 12 else month_start.replace(year=month_start.year + 1, month=1))
    calendar_html = '<table id="calendarTable" style="border-collapse: collapse;"><tr><th colspan="7">' + month_start.strftime('%B %Y') + '</th></tr><tr>'
    for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
        calendar_html += f'<th>{day}</th>'
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
        calendar_html += f'<td style="background-color: {bg_color}; {border_style}; text-align: center; cursor: pointer; color: {text_color};" onclick="selectDate(\'{date_str}\');">{current_day.day}</td>'
        if current_day.weekday() == 6:
            calendar_html += '</tr><tr>'
        current_day += timedelta(days=1)
    calendar_html += '</tr></table>'

    map_id = m.get_name()
    layer_groups_json = {k: v.get_name() for k, v in layer_groups.items()}
    
    legend_html = '''
    <div style="position: fixed; top: 10px; left: 10px; width: 300px; height: auto; background-color: white; border: 2px solid grey; z-index: 9999; font-size: 14px; padding: 10px; box-shadow: 2px 2px 6px rgba(0,0,0,0.3);">
        <b>Weather Risk Legend</b><br>
        <i style="background: #5aac91; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> Low risk<br>
        <i style="background: yellow; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> Slight risk<br>
        <i style="background: orange; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> Enhanced risk<br>
        <i style="background: red; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> Moderate risk<br>
        <i style="background: purple; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> High risk<br>
        <i style="background: black; width: 18px; height: 18px; float: left; margin-right: 8px;"></i> Other risks (flooding, hail, tornado, gusts)<br>
        <br><b>Select Convective Outlook:</b><br>
        <select id="kmlDropdown" onchange="showLayer(this.value);"></select>
        <br><br><b>Convective Discussion:</b><br>
        <div id="discussionText" style="max-height: 150px; overflow-y: auto; border: 1px solid #ccc; padding: 5px;">Select an outlook to view discussion.</div>
        <br><b>Risk Calendar:</b><br>
        <select id="monthDropdown" onchange="updateCalendar();">{0}</select>
        <select id="yearDropdown" onchange="updateCalendar();">{1}</select>
        <br><br>
        <div id="calendarContainer">{2}</div>
    </div>
    <script>
    var layerGroups = {3};
    var layerDefinitions = {4};
    var activeLayers = {{}};
    var dateRisks = {5};
    var riskColorsCal = {6};
    var defaultLayerId = '{7}';
    var riskPriority = {8};

    function showLayer(layerId) {{
        var map = {9};
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
        }} else {{
            document.getElementById('discussionText').innerHTML = 'Select an outlook to view discussion.';
        }}
        document.getElementById('kmlDropdown').value = layerId;
    }}

    function updateCalendar() {{
        var month = parseInt(document.getElementById('monthDropdown').value);
        var year = parseInt(document.getElementById('yearDropdown').value);
        console.log('Updating calendar for month:', month, 'year:', year);
        var monthStart = new Date(year, month - 1, 1);
        var nextMonth = new Date(year, month, 1);
        var firstDay = monthStart.getDay() === 0 ? 6 : monthStart.getDay() - 1;
        var currentDay = new Date(monthStart);
        var calendarHtml = '<table style="border-collapse: collapse;" id="calendarTable"><tr><th colspan="7">' + 
            monthStart.toLocaleString('default', {{month: 'long'}}) + ' ' + year + 
            '</th></tr><tr>';
        var days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        for (var day of days) {{
            calendarHtml += '<th>' + day + '</th>';
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
                if (bgColor === 'red' || bgColor === 'purple') {{
                    textColor = 'white';
                }}
            }}
            calendarHtml += '<td style="background-color: ' + bgColor + '; ' + borderStyle + '; text-align: center; cursor: pointer; color: ' + textColor + ';" ' +
                'onclick="selectDate(\\\'' + dateStr + '\\\');">' + currentDay.getDate() + '</td>';
            if (currentDay.getDay() === 0) {{
                calendarHtml += '</tr><tr>';
            }}
            currentDay.setDate(currentDay.getDate() + 1);
        }}
        calendarHtml += '</tr></table>';
        console.log('Replacing calendar HTML');
        document.getElementById('calendarContainer').innerHTML = calendarHtml;
    }}

    function selectDate(dateStr) {{
        console.log('Date selected: ' + dateStr);
        var dropdown = document.getElementById('kmlDropdown');
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

    document.addEventListener('DOMContentLoaded', function() {{
        console.log('DOM loaded, initializing with default layer: ' + defaultLayerId);
        showLayer(defaultLayerId);
        updateCalendar();
        selectDate('{10}');
    }});
    </script>
    '''.format(
        month_options,
        year_options,
        calendar_html,
        json.dumps(layer_groups_json, ensure_ascii=False),
        json.dumps(layer_definitions, ensure_ascii=False),
        json.dumps(date_risks, ensure_ascii=False),
        json.dumps(risk_colors_cal, ensure_ascii=False),
        default_layer_id,
        json.dumps(risk_priority, ensure_ascii=False),
        map_id,
        current_date.strftime('%Y-%m-%d')
    )
    m.get_root().html.add_child(folium.Element(legend_html))
    return m

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

# 9. Save Map as HTML for Sharing
def save_interactive_map(map_object, html_output_path, preview_image_name="map_preview.png", 
                         github_repo_url="https://raw.githubusercontent.com/handry6/ConvectiveOutlookNew/main"):
    map_object.save(html_output_path)
    with open(html_output_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    og_tags = f'''
    <meta property="og:title" content="Handry Outlook Convective Outlook">
    <meta property="og:description" content="Interactive map showing thunderstorms risks across the UK.">
    <meta property="og:image" content="{github_repo_url}/{preview_image_name}">
    <meta property="og:url" content="https://handry6.github.io/ConvectiveOutlookNew/{html_output_path}">
    <meta property="og:type" content="website">
    '''
    updated_html = html_content.replace('<head>', f'<head>\n{og_tags}')
    
    with open(html_output_path, 'w', encoding='utf-8') as f:
        f.write(updated_html)
    print(f"Interactive map saved as {html_output_path} with Open Graph tags")
    return html_output_path

# Main execution
if __name__ == "__main__":
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

    print(f"Scanning UK Weather directory and subdirectories ({uk_weather_dir}) for 2022-2025:")
    all_files = []
    for root, dirs, files in os.walk(uk_weather_dir):
        rel_root = os.path.relpath(root, uk_weather_dir)
        if rel_root == '.' or re.match(r'202[2-5](?:\\.*)?$', rel_root):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, uk_weather_dir)
                all_files.append(rel_path)
                print(f" - {rel_path}")

    kml_files = []
    for root, _, files in os.walk(uk_weather_dir):
        rel_root = os.path.relpath(root, uk_weather_dir)
        if rel_root == '.' or re.match(r'202[2-5](?:\\.*)?$', rel_root):
            for f in files:
                if re.match(r"Convective Outlook(?: UPDAT(?:E|ED)(\d+)?)? \d{8} \d{4} - \d{8} \d{4}(?:\s*\(\d+\))?\.kml", f, flags=re.IGNORECASE):
                    kml_files.append(os.path.join(root, f))

    if not kml_files:
        print("No KML files found matching the pattern in UK Weather/202[2-5] directories!")
        exit(1)

    generate_discussion_template(kml_files, discussion_file)
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

    print("Detected Convective Outlooks (KML files):")
    date_version_counts = defaultdict(int)
    for i, (kml, (start, end, data, version)) in enumerate(all_kmls_data.items(), 1):
        base_date = start.strftime('%d/%m/%Y')
        version_num = int(version)
        date_version_counts[base_date] = max(date_version_counts[base_date], version_num)
        version_label = f"{base_date} Version {version_num}"
        print(f"{i}. {kml} -> {version_label}")

    map_obj = create_mapbox_map(all_kmls_data, mapbox_access_token, uk_bounds, current_date)
    export_map_image(map_obj, output_map_img)
    overlay_on_template(output_map_img, template_img, final_output, position=(3236, 0))
    save_interactive_map(map_obj, interactive_map_html, preview_image_name=preview_image)

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    github_url = f"https://handry6.github.io/ConvectiveOutlookNew/{interactive_map_html}"
    try:
        os.chdir(repo_dir)
        subprocess.run(["git", "add", interactive_map_html], check=True)
        subprocess.run(["git", "add", final_output], check=True)
        subprocess.run(["git", "add", preview_image], check=True)
        subprocess.run(["git", "commit", "-m", "Update interactive map with preview image"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("Interactive map and preview image successfully pushed to GitHub!")
        webbrowser.open(github_url)
    except subprocess.CalledProcessError as e:
        print(f"Git operation failed: {e}")
        print("Ensure Git is set up correctly and your PAT is valid.")
        print(f"URL not opened: {github_url}")
    except Exception as e:
        print(f"Failed to open browser: {e}")
        print(f"URL not opened: {github_url}")

    print(f"Final image saved as {final_output}")