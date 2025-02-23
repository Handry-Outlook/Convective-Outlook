# Required libraries
import geopandas as gpd
import folium
import pandas as pd  # Added for pd.concat
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
    # Define risk priority (lower number = higher priority)
    risk_priority = {
        'Low risk': 6,
        'Slight risk': 5,
        'Enhanced risk': 4,
        'Moderate risk': 3,
        'High risk': 2,
        
    }

    # Add priority column to GeoDataFrame
    kml_data['priority'] = kml_data['Name'].map(risk_priority).fillna(0)
    
    # Sort by priority (highest first) so higher risks override lower ones
    kml_data = kml_data.sort_values(by='priority', ascending=False)
    
    # Create a new GeoDataFrame to store cleaned geometries
    cleaned_gdf = gpd.GeoDataFrame(columns=kml_data.columns, crs=kml_data.crs)
    
    # Process each polygon, subtracting it from all lower-priority polygons
    for idx, row in kml_data.iterrows():
        # Start with the current geometry
        current_geom = row['geometry']
        
        # Subtract this geometry from all lower-priority rows already processed
        for _, lower_row in cleaned_gdf.iterrows():
            if lower_row['priority'] < row['priority']:
                current_geom = current_geom.difference(lower_row['geometry'])
        
        # Only add non-empty geometries
        if not current_geom.is_empty:
            # Create a single-row GeoDataFrame and concatenate it
            new_row = gpd.GeoDataFrame([row.drop('geometry').to_dict() | {'geometry': current_geom}], crs=kml_data.crs)
            cleaned_gdf = pd.concat([cleaned_gdf, new_row], ignore_index=True)
    
    print("Cleaned GeoDataFrame:", cleaned_gdf.head())
    return cleaned_gdf

# 3. Create a Mapbox Map with a Single Layer
def create_mapbox_map(kml_data, mapbox_access_token, uk_bounds):
    # Define UK bounds
    uk_min_lat, uk_max_lat = 47, 62
    uk_min_lon, uk_max_lon = -11, 3
    #center_lat = (uk_max_lat + uk_min_lat) / 2
    #center_lon = (uk_max_lon + uk_min_lon) / 2
    center_lat = 54.013176
    center_lon = 2.3252278

    # Create a folium map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=9,
        max_bounds=[[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]],
        min_zoom=9,
        max_zoom=9,
        tiles="https://api.mapbox.com/styles/v1/handry-outlook/cm6dn4z49008801s2f3qd29he/draft/tiles/{z}/{x}/{y}?access_token=" + mapbox_access_token,
        attr='Mapbox'
    )

    # Define color mapping for risk categories
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

    # Define outline-only risks
    outline_only = [
        'Risk of flooding', 'Risk of large hail', 'Risk of tornado',
        'Risk of strong gusts', 'Risk of large hail, flooding and tornado'
    ]

    # Add a single GeoJson layer with dynamic styling
    folium.GeoJson(
        kml_data,
        style_function=lambda feature: {
            'fillColor': risk_colors.get(feature['properties']['Name'], 'gray'),
            'color': risk_colors.get(feature['properties']['Name'], 'gray'),
            'weight': 2 if feature['properties']['Name'] in outline_only else 1,
            'fillOpacity': 0.0 if feature['properties']['Name'] in outline_only else 0.3,
        }
    ).add_to(m)

    # Fit the map to the bounds
    bounds = [[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]]
    folium.FitBounds(bounds).add_to(m)

    return m

# 4. Export Map as Image using Selenium
def export_map_image(map_object, output_path, width=3876, height=5016):
    map_object.save("temp_map.html")
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(f'--window-size={width},{height}')
    chrome_options.add_argument('--force-device-scale-factor=1')
    driver = webdriver.Chrome(options=chrome_options, service=webdriver.chrome.service.Service(ChromeDriverManager().install()))
    driver.get(f"file://{os.path.abspath('temp_map.html')}")
    time.sleep(10)
    driver.save_screenshot(output_path)
    driver.quit()

# 5. Overlay on Template
def overlay_on_template(map_image_path, template_image_path, output_path, position=(0, 0)):
    template = Image.open(template_image_path).convert('RGBA')
    map_img = Image.open(map_image_path).convert('RGBA')
    #map_img = map_img.resize((800, 600), Image.Resampling.LANCZOS)
    template.paste(map_img, position, map_img)
    template.save(output_path, 'PNG', quality=100, optimize=False)

# Main execution
if __name__ == "__main__":
    kml_file = "Convective Outlook UPDATED2 01082024 0000 - 01082024 2359.kml"
    template_img = "2024 convective outlook FINALISED 2.png"
    mapbox_access_token = "pk.eyJ1IjoiaGFuZHJ5LW91dGxvb2siLCJhIjoiY2xrbnNrbmVlMXo0NDNqa2d3MTY2NW90bCJ9.AJbccNwtKvKA8il3JkE3PA"
    output_map_img = "temp_map.png"
    final_output = "final_output.png"

    uk_bounds = {'min_lat': 47.6, 'max_lat': 62.1, 'min_lon': -13.7, 'max_lon': 7.4}

    # Load and process KML
    kml_data = load_kml(kml_file)
    cleaned_kml_data = prioritize_and_clean_kml_data(kml_data)
    
    # Create and export map
    map_obj = create_mapbox_map(cleaned_kml_data, mapbox_access_token, uk_bounds)
    export_map_image(map_obj, output_map_img)
    
    # Overlay on template
    overlay_on_template(output_map_img, template_img, final_output, position=(3236, 0))

    print(f"Final image saved as {final_output}")