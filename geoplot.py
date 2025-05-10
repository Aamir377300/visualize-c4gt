"""
geoplot.py
----------

This visualization renders a 3-D plot of the data given the state
trajectory of a simulation, and the path of the property to render.

It generates an HTML file that contains code to render the plot
using Cesium Ion, and the GeoJSON file of data provided to the plot.

An example of its usage is as follows:

```py
from agent_torch.visualize import GeoPlot

# create a simulation
# ...

# create a visualizer
engine = GeoPlot(config, {
  cesium_token: "...",
  step_time: 3600,
  coordinates = "agents/consumers/coordinates",
  feature = "agents/consumers/money_spent",
})

# visualize in the runner-loop
for i in range(0, num_episodes):
  runner.step(num_steps_per_episode)
  engine.render(runner.state_trajectory)
```
"""

import re
import json

import pandas as pd
import numpy as np

from string import Template
from agent_torch.core.helpers import get_by_path

geoplot_template = """
<!doctype html>
<html lang="en">
  <head>
    <!-- Meta tags to set the character encoding and responsive viewport settings -->
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Cesium Time-Series Heatmap Visualization</title>
    <!-- Including CesiumJS and its CSS for the 3D map visualization -->
    <script src="https://cesium.com/downloads/cesiumjs/releases/1.95/Build/Cesium/Cesium.js"></script>
    <link href="https://cesium.com/downloads/cesiumjs/releases/1.95/Build/Cesium/Widgets/widgets.css" rel="stylesheet" />
    <style>
      /* Style for the Cesium container to take full height and width of the page */
      #cesiumContainer {
        width: 100%;
        height: 100%;
      }
    </style>
  </head>
  <body>
    <!-- The div where the Cesium map will be rendered -->
    <div id="cesiumContainer"></div>

    <script>
      // Assigning the Cesium Ion access token from the Python-generated template variable
      Cesium.Ion.defaultAccessToken = '$accessToken'

      // Create the Cesium viewer to display the map
      const viewer = new Cesium.Viewer('cesiumContainer')

      // Function to interpolate between two colors based on a factor
      function interpolateColor(color1, color2, factor) {
        const result = new Cesium.Color()
        result.red = color1.red + factor * (color2.red - color1.red)
        result.green = color1.green + factor * (color2.green - color1.green)
        result.blue = color1.blue + factor * (color2.blue - color1.blue)
        result.alpha = '$visualType' == 'size' ? 0.2 : color1.alpha + factor * (color2.alpha - color1.alpha)
        return result
      }

      // Function to calculate the color based on value, min, and max for the heatmap
      function getColor(value, min, max) {
        const factor = (value - min) / (max - min)
        return interpolateColor(Cesium.Color.BLUE, Cesium.Color.RED, factor)
      }

      // Function to calculate pixel size based on value, min, and max for visual scaling
      function getPixelSize(value, min, max) {
        const factor = (value - min) / (max - min)
        return 100 * (1 + factor)
      }

      // Function to process the time-series GeoJSON data and return time-series map
      function processTimeSeriesData(geoJsonData) {
        const timeSeriesMap = new Map()
        let minValue = Infinity
        let maxValue = -Infinity

        geoJsonData.features.forEach((feature) => {
          const id = feature.properties.id
          const time = Cesium.JulianDate.fromIso8601(feature.properties.time)
          const value = feature.properties.value
          const coordinates = feature.geometry.coordinates

          // Add feature data to time-series map
          if (!timeSeriesMap.has(id)) {
            timeSeriesMap.set(id, [])
          }
          timeSeriesMap.get(id).push({ time, value, coordinates })

          // Track min and max values for scaling
          minValue = Math.min(minValue, value)
          maxValue = Math.max(maxValue, value)
        })

        return { timeSeriesMap, minValue, maxValue }
      }

      // Function to create Cesium entities (points) for the time-series data
      function createTimeSeriesEntities(timeSeriesData, startTime, stopTime) {
        const dataSource = new Cesium.CustomDataSource('AgentTorch Simulation')

        // Iterate through the time-series data and create entities for visualization
        for (const [id, timeSeries] of timeSeriesData.timeSeriesMap) {
          const entity = new Cesium.Entity({
            id: id,
            availability: new Cesium.TimeIntervalCollection([
              new Cesium.TimeInterval({
                start: startTime,
                stop: stopTime,
              }),
            ]),
            position: new Cesium.SampledPositionProperty(),
            point: {
              pixelSize: '$visualType' == 'size' ? new Cesium.SampledProperty(Number) : 10,
              color: new Cesium.SampledProperty(Cesium.Color),
            },
            properties: {
              value: new Cesium.SampledProperty(Number),
            },
          })

          // Add samples of time, position, color, and size to the entity
          timeSeries.forEach(({ time, value, coordinates }) => {
            const position = Cesium.Cartesian3.fromDegrees(coordinates[0], coordinates[1])
            entity.position.addSample(time, position)
            entity.properties.value.addSample(time, value)
            entity.point.color.addSample(
              time,
              getColor(value, timeSeriesData.minValue, timeSeriesData.maxValue)
            )

            if ('$visualType' == 'size') {
              entity.point.pixelSize.addSample(
                time,
                getPixelSize(value, timeSeriesData.minValue, timeSeriesData.maxValue)
              )
            }
          })

          dataSource.entities.add(entity)
        }

        return dataSource
      }

      // Example time-series GeoJSON data passed from Python
      const geoJsons = $data

      // Start and stop times for the Cesium viewer, passed from Python
      const start = Cesium.JulianDate.fromIso8601('$startTime')
      const stop = Cesium.JulianDate.fromIso8601('$stopTime')

      // Setting up the viewer's clock
      viewer.clock.startTime = start.clone()
      viewer.clock.stopTime = stop.clone()
      viewer.clock.currentTime = start.clone()
      viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP
      viewer.clock.multiplier = 3600 // 1 hour per second
      viewer.timeline.zoomTo(start, stop)

      // Loop through the GeoJSON data and create visual entities for the time-series data
      for (const geoJsonData of geoJsons) {
        const timeSeriesData = processTimeSeriesData(geoJsonData)
        const dataSource = createTimeSeriesEntities(timeSeriesData, start, stop)
        viewer.dataSources.add(dataSource)
        viewer.zoomTo(dataSource)
      }
    </script>
  </body>
</html>

"""

# Helper function to access nested values in the state dictionary using '/'-separated path
def read_var(state, var):
    return get_by_path(state, re.split("/", var))  # Splits 'agents/consumers/value' and retrieves nested data

# Class to generate a 3D geospatial visualization of simulation data using Cesium
class GeoPlot:
    def __init__(self, config, options):
        self.config = config  # Simulation configuration

        # Extracting required parameters from options
        (
            self.cesium_token,         # Cesium Ion API token for rendering 3D maps
            self.step_time,            # Time interval between steps (in seconds)
            self.entity_position,      # Path to entity positions (latitude, longitude)
            self.entity_property,      # Path to property to be visualized (e.g., consumption)
            self.visualization_type,   # Type of visualization ('color' or 'size')
        ) = (
            options["cesium_token"],
            options["step_time"],
            options["coordinates"],
            options["feature"],
            options["visualization_type"],
        )

    # Render method generates geoJSON data and outputs an interactive HTML map
    def render(self, state_trajectory):
        coords, values = [], []  # Lists to store entity coordinates and property values

        # Get simulation name to generate filenames
        name = self.config["simulation_metadata"]["name"]
        geodata_path, geoplot_path = f"{name}.geojson", f"{name}.html"

        # Iterate over all episodes in the simulation trajectory
        for i in range(0, len(state_trajectory) - 1):
            final_state = state_trajectory[i][-1]  # Get last state of the episode

            # Extract coordinates of entities
            coords = np.array(read_var(final_state, self.entity_position)).tolist()

            # Extract and flatten the desired property values (e.g., money spent, power used)
            values.append(
                np.array(read_var(final_state, self.entity_property)).flatten().tolist()
            )

        # Generate timestamps based on step_time and total simulation steps
        start_time = pd.Timestamp.utcnow()  # Use current UTC time as simulation start
        timestamps = [
            start_time + pd.Timedelta(seconds=i * self.step_time)
            for i in range(
                self.config["simulation_metadata"]["num_episodes"] *
                self.config["simulation_metadata"]["num_steps_per_episode"]
            )
        ]

        geojsons = []  # Will store the full GeoJSON output

        # For each entity, construct its full list of time-based features
        for i, coord in enumerate(coords):
            features = []  # Individual time series for one entity

            # For each timestamp, assign the entity's property value
            for time, value_list in zip(timestamps, values):
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [coord[1], coord[0]],  # [longitude, latitude]
                        },
                        "properties": {
                            "value": value_list[i],      # Property value at that time
                            "time": time.isoformat(),    # Timestamp in ISO format
                        },
                    }
                )

            # Add entity's full timeline as a FeatureCollection
            geojsons.append({"type": "FeatureCollection", "features": features})

        # Write GeoJSON data to file for debugging or external use
        with open(geodata_path, "w", encoding="utf-8") as f:
            json.dump(geojsons, f, ensure_ascii=False, indent=2)

        # Fill the HTML template with simulation data and save it
        tmpl = Template(geoplot_template)  # geoplot_template should be defined elsewhere as HTML string
        with open(geoplot_path, "w", encoding="utf-8") as f:
            f.write(
                tmpl.substitute(
                    {
                        "accessToken": self.cesium_token,              # Cesium token
                        "startTime": timestamps[0].isoformat(),        # Simulation start time
                        "stopTime": timestamps[-1].isoformat(),        # Simulation end time
                        "data": json.dumps(geojsons),                  # Full entity time series
                        "visualType": self.visualization_type,         # Visual style (e.g., color/size)
                    }
                )
            )
