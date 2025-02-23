# Required libraries
import geopandas as gpd
import folium
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import time
import os

# 1. Load and Parse the KML File
def load_kml(kml_file_path):
    gdf = gpd.read_file(kml_file_path)
    gdf = gdf.to_crs(epsg=4326)  # WGS84
    print("KML DataFrame columns:", gdf.columns)
    print("First few rows of KML data:", gdf.head())
    return gdf

# 2. Pre-process KML Data to Resolve Overlaps with Priority
def prioritize_and_clean_kml_data(kml_data):
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

# 3. Create a Mapbox Map with a Single Layer
def create_mapbox_map(kml_data, mapbox_access_token, uk_bounds):
    uk_min_lat, uk_max_lat = 47, 62
    uk_min_lon, uk_max_lon = -11, 3
    center_lat = 54.013176
    center_lon = 2.3252278
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=7,
        max_bounds=[[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]],
        min_zoom=7,
        max_zoom=7,
        tiles="https://api.mapbox.com/styles/v1/handry-outlook/cm6dn4z49008801s2f3qd29he/draft/tiles/{z}/{x}/{y}?access_token=" + mapbox_access_token,
        attr='Mapbox'
    )
    risk_colors = {
        'Low risk': '#5aac91',
        'Slight risk': 'yellow',
        'Enhanced risk': 'orange',
        'Moderate risk': 'red',
        'High risk': 'purple',
        'Risk of flooding': 'black',
        'Risk of large hail': 'black',
        'Risk of tornado': 'black',
        'Risk of strong gusts': 'black',
        'Risk of large hail, flooding and tornado': 'black'
    }
    outline_only = [
        'Risk of flooding', 'Risk of large hail', 'Risk of tornado',
        'Risk of strong gusts', 'Risk of large hail, flooding and tornado'
    ]
    folium.GeoJson(
        kml_data,
        style_function=lambda feature: {
            'fillColor': risk_colors.get(feature['properties']['Name'], 'gray'),
            'color': risk_colors.get(feature['properties']['Name'], 'gray'),
            'weight': 2 if feature['properties']['Name'] in outline_only else 1,
            'fillOpacity': 0.0 if feature['properties']['Name'] in outline_only else 0.3,
        }
    ).add_to(m)
    bounds = [[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]]
    folium.FitBounds(bounds).add_to(m)
    return m

# 4. Export Map as Image using Selenium
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

# 5. Overlay Template on Map
def overlay_on_template(map_image_path, template_image_path, output_path, position=(0, 0)):
    map_img = Image.open(map_image_path).convert('RGBA')
    template = Image.open(template_image_path).convert('RGBA')
    map_img.paste(template, position, template)
    map_img.save(output_path, 'PNG', quality=100, optimize=False)

# 6. Save Map as HTML for Sharing
def save_interactive_map(map_object, html_output_path):
    map_object.save(html_output_path)
    print(f"Interactive map saved as {html_output_path}")
    return html_output_path

# Main execution
if __name__ == "__main__":
    # Paths relative to the script's directory
    kml_file = "Convective Outlook UPDATED2 01082024 0000 - 01082024 2359.kml"
    template_img = "2024 convective outlook FINALISED 2.png"
    mapbox_access_token = "pk.eyJ1IjoiaGFuZHJ5LW91dGxvb2siLCJhIjoiY2xrbnNrbmVlMXo0NDNqa2d3MTY2NW90bCJ9.AJbccNwtKvKA8il3JkE3PA"
    output_map_img = "temp_map.png"
    final_output = "final_output.png"
    interactive_map_html = "interactive_map.html"

    uk_bounds = {'min_lat': 47.6, 'max_lat': 62.1, 'min_lon': -13.7, 'max_lon': 7.4}

    # Load and process KML
    kml_data = load_kml(kml_file)
    cleaned_kml_data = prioritize_and_clean_kml_data(kml_data)
    
    # Create map
    map_obj = create_mapbox_map(cleaned_kml_data, mapbox_access_token, uk_bounds)
    
    # Export map as image
    export_map_image(map_obj, output_map_img)
    
    # Overlay template on map
    overlay_on_template(output_map_img, template_img, final_output, position=(3236, 0))
    
    # Save interactive map as HTML
    save_interactive_map(map_obj, interactive_map_html)

    print(f"Final image saved as {final_output}")
    print(f"Interactive map saved as {interactive_map_html}. Commit and push to GitHub to update the hosted version.")