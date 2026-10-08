// Drought-Based Smart Subsidy System - real data export, version 2
// Paste this whole script into https://code.earthengine.google.com and press Run.
// It does everything the first script did, and also exports:
//   - an NDVI trend for the season (MODIS, six 16-day periods, this year vs normal)  -> early warning
//   - soil moisture anomaly and maximum temperature anomaly (ERA5-Land)              -> context indicators
//
// Output: a CSV in your Google Drive with one row per sampled cropland point.

// ---------------- settings you can change ----------------
var NUM_PLOTS = 300;          // lower this to 150 if you get a "computation timed out" error
var SEASON_YEAR = 2026;
var NDVI_START = '-08-01';    // Sentinel-2 window start (month-day)
var NDVI_END = '-10-01';      // end is exclusive, so this covers all of September
var RAIN_START = '-06-01';
var RAIN_END = '-09-01';      // CHIRPS only had data up to Aug 31 when we ran v1; raise it later if more days exist
var NORMAL_NDVI_YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025];
var NORMAL_RAIN_YEARS = [];
for (var y = 1991; y <= 2020; y++) { NORMAL_RAIN_YEARS.push(y); }
var NORMAL_ERA_YEARS = [];
for (var e = 2001; e <= 2020; e++) { NORMAL_ERA_YEARS.push(e); }

// MODIS 16-day composites start on these days of the year (June 26, Jul 12, Jul 28, Aug 13, Aug 29, Sep 14)
var TS_STARTS = [177, 193, 209, 225, 241, 257];
var NORMAL_MODIS_FIRST = 2005;
var NORMAL_MODIS_LAST = 2025;

// ---------------- study area ----------------
var district = ee.FeatureCollection('FAO/GAUL/2015/level2')
  .filter(ee.Filter.and(
    ee.Filter.eq('ADM1_NAME', 'Maharashtra'),
    ee.Filter.eq('ADM2_NAME', 'Aurangabad')));
print('Districts matched (should be 1):', district.size());
var region = district.geometry();
Map.centerObject(region, 8);
Map.addLayer(region, {color: 'blue'}, 'District');

// ---------------- cropland sample points ----------------
var cropland = ee.ImageCollection('ESA/WorldCover/v200').first().eq(40).selfMask();
var points = cropland.sample({
  region: region,
  scale: 100,
  numPixels: NUM_PLOTS,
  seed: 7,
  geometries: true
});
Map.addLayer(points, {color: 'red'}, 'Sample plots');

// ---------------- NDVI this season vs normal (Sentinel-2, 10 m) ----------------
function maskAndIndex(img) {
  var scl = img.select('SCL');
  var bad = scl.eq(1).or(scl.eq(3)).or(scl.eq(8)).or(scl.eq(9)).or(scl.eq(10)).or(scl.eq(11));
  var ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI').updateMask(bad.not());
  var valid = bad.not().rename('valid').toFloat();
  return ndvi.addBands(valid);
}
function s2Season(year) {
  return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(region)
    .filterDate(year + NDVI_START, year + NDVI_END)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 70))
    .map(maskAndIndex);
}
var current = s2Season(SEASON_YEAR);
print('Sentinel-2 scenes used for ' + SEASON_YEAR + ':', current.size());
var ndviCurrent = current.select('NDVI').median();
var cloudPct = ee.Image(1).subtract(current.select('valid').mean()).multiply(100);
var ndviNormal = ee.ImageCollection(NORMAL_NDVI_YEARS.map(function (yr) {
  return s2Season(yr).select('NDVI').median();
})).mean();

// ---------------- rainfall (CHIRPS, about 5 km) ----------------
var chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').select('precipitation');
function rainSum(year) {
  return chirps.filterDate(year + RAIN_START, year + RAIN_END).sum();
}
print('CHIRPS days available in ' + SEASON_YEAR + ' window:',
  chirps.filterDate(SEASON_YEAR + RAIN_START, SEASON_YEAR + RAIN_END).size());
var rainActual = rainSum(SEASON_YEAR);
var rainNormal = ee.ImageCollection(NORMAL_RAIN_YEARS.map(rainSum)).mean();

// ---------------- soil moisture and temperature (ERA5-Land, about 11 km) ----------------
var era = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_AGGR');
function eraMean(year, band) {
  return era.filterDate(year + NDVI_START, year + NDVI_END).select(band).mean();
}
function eraAnomaly(band, asPercent) {
  var cur = eraMean(SEASON_YEAR, band);
  var norm = ee.ImageCollection(NORMAL_ERA_YEARS.map(function (yr) { return eraMean(yr, band); })).mean();
  var diff = cur.subtract(norm);
  return asPercent ? diff.divide(norm).multiply(100) : diff;
}
var soilAnomaly = eraAnomaly('volumetric_soil_water_layer_1', true).rename('soil_moisture_anomaly_pct');
var tempAnomaly = eraAnomaly('temperature_2m_max', false).rename('temp_max_anomaly_c');

// ---------------- NDVI trend (MODIS, 250 m, 16-day composites) ----------------
var modis = ee.ImageCollection('MODIS/061/MOD13Q1').map(function (img) {
  var ok = img.select('SummaryQA').lte(1);   // 0 good, 1 marginal; drops snow/ice and cloudy
  return img.select('NDVI').multiply(0.0001).updateMask(ok).rename('NDVI')
    .copyProperties(img, ['system:time_start']);
});
// mean of a collection, or a fully masked image if the collection is empty (data not published yet)
function safeMean(coll, name) {
  var empty = ee.Image.constant(0).rename(name).selfMask();
  return ee.Image(ee.Algorithms.If(coll.size().gt(0), coll.mean().rename(name), empty));
}
var trend = ee.Image([]);
TS_STARTS.forEach(function (doy, i) {
  var inWindow = modis.filter(ee.Filter.calendarRange(doy, doy + 15, 'day_of_year'));
  var cur = inWindow.filter(ee.Filter.calendarRange(SEASON_YEAR, SEASON_YEAR, 'year'));
  var norm = inWindow.filter(ee.Filter.calendarRange(NORMAL_MODIS_FIRST, NORMAL_MODIS_LAST, 'year'));
  trend = trend.addBands(safeMean(cur, 'ts_cur_' + (i + 1)))
               .addBands(safeMean(norm, 'ts_norm_' + (i + 1)));
});

// ---------------- sample everything at the points ----------------
var stack = ndviCurrent.rename('ndvi_current')
  .addBands(ndviNormal.rename('ndvi_normal'))
  .addBands(rainActual.rename('rain_actual_mm'))
  .addBands(rainNormal.rename('rain_normal_mm'))
  .addBands(cloudPct.rename('cloud_cover_pct'))
  .addBands(soilAnomaly)
  .addBands(tempAnomaly)
  .addBands(trend);

var result = stack.reduceRegions({
  collection: points,
  reducer: ee.Reducer.first(),
  scale: 30,
  tileScale: 8
}).map(function (f) {
  var c = f.geometry().coordinates();
  return f.set({lon: c.get(0), lat: c.get(1)});
}).filter(ee.Filter.notNull(['ndvi_current', 'ndvi_normal', 'rain_actual_mm', 'rain_normal_mm']));

print('First few rows:', result.limit(5));

var columns = ['lat', 'lon', 'ndvi_current', 'ndvi_normal', 'rain_actual_mm', 'rain_normal_mm',
  'cloud_cover_pct', 'soil_moisture_anomaly_pct', 'temp_max_anomaly_c'];
for (var k = 1; k <= TS_STARTS.length; k++) { columns.push('ts_cur_' + k); }
for (var m = 1; m <= TS_STARTS.length; m++) { columns.push('ts_norm_' + m); }

Export.table.toDrive({
  collection: result,
  description: 'sambhajinagar_plots_2026_v2',
  fileFormat: 'CSV',
  selectors: columns
});
// After Run: open the "Tasks" tab (top right) and click RUN next to the export.
