# Required libraries
import geopandas as gpd
import folium
from folium.plugins import MarkerCluster
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import time
import os

# 1. Load and Parse the KML File
def load_kml(kml_file_path):
    # Read the KML file using geopandas
    gdf = gpd.read_file(kml_file_path)
    
    # Ensure the geometry is in a suitable CRS (e.g., WGS84 for Mapbox)
    gdf = gdf.to_crs(epsg=4326)  # WGS84
    return gdf

# 2. Create a Mapbox Map with KML Polygons and Risk Categories
def create_mapbox_map(kml_data, mapbox_access_token, uk_bounds):
    # Define UK bounds (example: latitude and longitude for UK)
    uk_min_lat, uk_max_lat = 47,62  # Updated UK latitude range
    uk_min_lon, uk_max_lon = -11, 3   # Updated UK longitude range

    # Calculate center for the map
    center_lat = (uk_max_lat + uk_min_lat) / 2
    center_lon = (uk_max_lon + uk_min_lon) / 2

    # Create a folium map centered on the UK with tighter bounds
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=9,
        max_bounds=[[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]],  # Restrict map to UK bounds
        min_zoom=9,
        max_zoom=9,
        tiles="https://api.mapbox.com/styles/v1/mapbox/streets-v11/tiles/{z}/{x}/{y}?access_token=" + mapbox_access_token,
        attr='Mapbox'
    )

    # Define color mapping for risk categories
    risk_colors = {
        'Low risk': 'green',
        'Slight risk': 'yellow',
        'Enhanced risk': 'orange',
        'Moderate risk': 'red',
        'High risk': 'purple',
        'Risk of flooding': 'black',  # Use black for outline only
        'Risk of large hail': 'black',  # Use black for outline only
        'Risk of tornado': 'black',  # Use black for outline only
        'Risk of strong gusts': 'black'  # Use black for outline only
    }

    # Add polygons to the map with appropriate styles
    for _, row in kml_data.iterrows():
        risk_name = row.get('name', 'Unknown')  # Assuming 'name' field contains the risk category
        color = risk_colors.get(risk_name, 'gray')  # Default to gray if risk name not found
        
        if risk_name in ['Risk of flooding', 'Risk of large hail', 'Risk of tornado', 'Risk of strong gusts']:
            # Draw only a black outline (no fill)
            folium.GeoJson(
                row.geometry,
                style_function=lambda feature: {
                    'fillOpacity': 0.0,  # No fill
                    'color': 'black',    # Black outline
                    'weight': 2,         # Thicker outline for visibility
                }
            ).add_to(m)
        else:
            # Draw filled polygons with color for other risk categories
            folium.GeoJson(
                row.geometry,
                style_function=lambda feature, color=color: {
                    'fillColor': color,
                    'color': color,
                    'weight': 1,
                    'fillOpacity': 0.5,
                }
            ).add_to(m)

    # Fit the map to the bounds of the KML data, but ensure it stays within UK bounds
    bounds = [[uk_min_lat, uk_min_lon], [uk_max_lat, uk_max_lon]]
    folium.FitBounds(bounds).add_to(m)

    return m

# 3. Export Map as Image using Selenium with Higher Resolution
def export_map_image(map_object, output_path, width=5000, height=7300):  # Increased for higher resolution (e.g., Full HD)
    # Save the map as an HTML file
    map_object.save("temp_map.html")
    
    # Set up Selenium with headless Chrome
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # Run in headless mode
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(f'--window-size={width},{height}')
    
    # Use ChromeDriverManager to automatically handle ChromeDriver installation
    driver = webdriver.Chrome(options=chrome_options, service=webdriver.chrome.service.Service(ChromeDriverManager().install()))
    
    # Load the HTML file
    driver.get(os.path.abspath("temp_map.html"))
    
    # Wait for the map to load (adjust time as needed)
    time.sleep(90)
    
    # Take a screenshot of the map
    driver.save_screenshot(output_path)
    
    # Close the driver
    driver.quit()

# 4. Overlay on Template
def overlay_on_template(map_image_path, template_image_path, output_path, position=(0, 0)):
    # Open the template image (your provided PNG)
    template = Image.open(template_image_path).convert('RGBA')
    
    # Open the map image
    map_img = Image.open(map_image_path).convert('RGBA')
    
    # Resize map image to fit the template (adjust as needed)
    # Use a larger size to maintain high resolution, but scale to fit template
    map_img = map_img.resize((800, 600), Image.Resampling.LANCZOS)  # Higher resolution, but scaled to fit; adjust as needed
    
    # Paste the map image onto the template at the specified position
    template.paste(map_img, position, map_img)
    
    # Save the result with high quality
    template.save(output_path, 'PNG', quality=95, optimize=True)

# Main execution
if __name__ == "__main__":
    # Replace these with your actual paths and token
    kml_file = "Convective Outlook UPDATED2 01082024 0000 - 01082024 2359.kml"
    template_img = "2024 convective outlook FINALISED 2.png"  # Your provided Convective Outlook PNG
    mapbox_access_token = "pk.eyJ1IjoiaGFuZHJ5LW91dGxvb2siLCJhIjoiY2xrbnNrbmVlMXo0NDNqa2d3MTY2NW90bCJ9.AJbccNwtKvKA8il3JkE3PA"
    output_map_img = "temp_map.png"
    final_output = "final_output.png"

    # UK bounds (use your adjusted values)
    uk_bounds = {
        'min_lat': 47.6,
        'max_lat': 62.1,
        'min_lon': -13.7,
        'max_lon': 7.4
    }

    # Load KML
    kml_data = load_kml(kml_file)
    
    # Create and export map
    map_obj = create_mapbox_map(kml_data, mapbox_access_token, uk_bounds)
    export_map_image(map_obj, output_map_img)
    
    # Overlay on template
    overlay_on_template(output_map_img, template_img, final_output, position=(50, 200))  # Adjust position as needed

    print(f"Final image saved as {final_output}")