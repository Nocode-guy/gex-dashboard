#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Net.Http;
using System.Threading.Tasks;
using System.Windows.Media;
using System.Xml.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class GEXZonesIndicator : Indicator
    {
        #region Variables
        private static readonly HttpClient httpClient = new HttpClient();
        private DateTime lastFetchTime = DateTime.MinValue;
        private List<GEXLevel> cachedLevels = new List<GEXLevel>();
        private bool isStale = false;
        private bool opexWarning = false;
        private double spotPrice = 0;
        private string lastError = "";

        // For drawing
        private HashSet<string> drawnTags = new HashSet<string>();
        #endregion

        #region GEX Level Class
        private class GEXLevel
        {
            public double Price { get; set; }
            public double GEX { get; set; }  // In billions
            public string Type { get; set; }  // "positive" or "negative"
            public string Role { get; set; }  // "king", "gatekeeper", "support", etc.
            public double Strength { get; set; }  // 0-1
        }
        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Displays GEX zones from the GEX Dashboard API";
                Name = "GEXZonesIndicator";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                DrawOnPricePanel = true;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = true;

                // Parameters
                ApiUrl = "http://localhost:5000";
                OptionsSymbol = "SPX";  // SPX for ES, use appropriate options symbol
                RefreshMinutes = 5;
                ZoneHeightTicks = 20;
                MaxZonesToShow = 8;

                // Colors
                PositiveZoneColor = Brushes.MediumSeaGreen;
                NegativeZoneColor = Brushes.MediumPurple;
                KingZoneColor = Brushes.Gold;
                GatekeeperZoneColor = Brushes.OrangeRed;
                BaseOpacity = 40;

                // Status display
                ShowStatusPanel = true;
                StatusFontSize = 11;
            }
            else if (State == State.DataLoaded)
            {
                // Initial fetch
                _ = FetchGEXLevelsAsync();
            }
            else if (State == State.Terminated)
            {
                // Cleanup
                drawnTags.Clear();
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < 10)
                return;

            // Check if we need to refresh
            if (ShouldRefresh())
            {
                _ = FetchGEXLevelsAsync();
            }

            // Draw zones
            DrawGEXZones();

            // Draw status panel
            if (ShowStatusPanel)
            {
                DrawStatusPanel();
            }
        }

        private bool ShouldRefresh()
        {
            return (DateTime.Now - lastFetchTime).TotalMinutes >= RefreshMinutes;
        }

        private async Task FetchGEXLevelsAsync()
        {
            try
            {
                string url = $"{ApiUrl}/gex/{OptionsSymbol}/levels";
                var response = await httpClient.GetStringAsync(url);

                var json = JObject.Parse(response);

                // Update state
                spotPrice = json["spot"]?.Value<double>() ?? 0;
                isStale = json["stale"]?.Value<bool>() ?? false;
                opexWarning = json["opex_warning"]?.Value<bool>() ?? false;

                // Parse levels
                cachedLevels.Clear();
                var levelsArray = json["levels"] as JArray;

                if (levelsArray != null)
                {
                    foreach (var level in levelsArray)
                    {
                        cachedLevels.Add(new GEXLevel
                        {
                            Price = level["price"]?.Value<double>() ?? 0,
                            GEX = level["gex"]?.Value<double>() ?? 0,
                            Type = level["type"]?.Value<string>() ?? "positive",
                            Role = level["role"]?.Value<string>() ?? "support",
                            Strength = level["strength"]?.Value<double>() ?? 0.5
                        });
                    }
                }

                lastFetchTime = DateTime.Now;
                lastError = "";

                Print($"[GEX] Fetched {cachedLevels.Count} levels for {OptionsSymbol}");
            }
            catch (Exception ex)
            {
                lastError = ex.Message;
                Print($"[GEX] Error fetching data: {ex.Message}");
            }
        }

        private void DrawGEXZones()
        {
            // Clear old drawings
            foreach (var tag in drawnTags)
            {
                RemoveDrawObject(tag);
            }
            drawnTags.Clear();

            if (cachedLevels.Count == 0)
                return;

            int zonesDrawn = 0;

            foreach (var level in cachedLevels)
            {
                if (zonesDrawn >= MaxZonesToShow)
                    break;

                // Calculate zone boundaries
                double zoneTop = level.Price + (ZoneHeightTicks * TickSize / 2.0);
                double zoneBottom = level.Price - (ZoneHeightTicks * TickSize / 2.0);

                // Determine colors based on type and role
                Brush zoneBrush;
                int opacity;

                if (level.Role == "king")
                {
                    zoneBrush = KingZoneColor;
                    opacity = (int)(BaseOpacity * 1.5);  // King is more prominent
                }
                else if (level.Role == "gatekeeper")
                {
                    zoneBrush = GatekeeperZoneColor;
                    opacity = (int)(BaseOpacity * 1.2);
                }
                else if (level.Type == "positive")
                {
                    zoneBrush = PositiveZoneColor;
                    opacity = (int)(BaseOpacity * level.Strength);
                }
                else
                {
                    zoneBrush = NegativeZoneColor;
                    opacity = (int)(BaseOpacity * level.Strength);
                }

                // Ensure minimum visibility
                opacity = Math.Max(15, Math.Min(80, opacity));

                // Draw rectangle
                string rectTag = $"GEX_Zone_{level.Price}_{CurrentBar}";
                int barsAgoStart = Math.Min(100, CurrentBar);

                Draw.Rectangle(this, rectTag,
                    false,
                    barsAgoStart, zoneTop,
                    0, zoneBottom,
                    zoneBrush, zoneBrush, opacity);

                drawnTags.Add(rectTag);

                // Draw label
                string labelText = GetZoneLabel(level);
                string labelTag = $"GEX_Label_{level.Price}_{CurrentBar}";

                Draw.Text(this, labelTag,
                    labelText,
                    0,
                    zoneTop + (2 * TickSize),
                    zoneBrush);

                drawnTags.Add(labelTag);

                zonesDrawn++;
            }
        }

        private string GetZoneLabel(GEXLevel level)
        {
            string gexStr = level.GEX >= 0 ? $"+{level.GEX:F1}B" : $"{level.GEX:F1}B";
            string roleStr = "";

            switch (level.Role)
            {
                case "king":
                    roleStr = " [KING]";
                    break;
                case "gatekeeper":
                    roleStr = " [GK]";
                    break;
            }

            return $"{level.Price:F2} | {gexStr}{roleStr}";
        }

        private void DrawStatusPanel()
        {
            // Determine status color
            Brush statusBrush;
            string statusText;

            if (!string.IsNullOrEmpty(lastError))
            {
                statusBrush = Brushes.Red;
                statusText = $"GEX: Error - {lastError}";
            }
            else if (isStale)
            {
                statusBrush = Brushes.Red;
                statusText = $"GEX: STALE DATA | {OptionsSymbol}";
            }
            else if (opexWarning)
            {
                statusBrush = Brushes.Orange;
                statusText = $"GEX: {OptionsSymbol} | OPEX WEEK | {cachedLevels.Count} zones";
            }
            else
            {
                statusBrush = Brushes.LimeGreen;
                statusText = $"GEX: {OptionsSymbol} | {cachedLevels.Count} zones | {lastFetchTime:HH:mm:ss}";
            }

            // Draw status in top-left
            Draw.TextFixed(this, "GEX_Status",
                statusText,
                TextPosition.TopLeft,
                statusBrush,
                new SimpleFont("Arial", StatusFontSize),
                Brushes.Transparent,
                Brushes.Transparent,
                0);
        }

        #region Properties

        [NinjaScriptProperty]
        [Display(Name = "API URL", Description = "GEX Dashboard API URL", Order = 1, GroupName = "Connection")]
        public string ApiUrl { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Options Symbol", Description = "Options symbol to fetch (SPX for ES, etc.)", Order = 2, GroupName = "Connection")]
        public string OptionsSymbol { get; set; }

        [NinjaScriptProperty]
        [Range(1, 60)]
        [Display(Name = "Refresh Minutes", Description = "How often to refresh data", Order = 3, GroupName = "Connection")]
        public int RefreshMinutes { get; set; }

        [NinjaScriptProperty]
        [Range(5, 100)]
        [Display(Name = "Zone Height (Ticks)", Description = "Height of each zone in ticks", Order = 1, GroupName = "Display")]
        public int ZoneHeightTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Max Zones", Description = "Maximum zones to display", Order = 2, GroupName = "Display")]
        public int MaxZonesToShow { get; set; }

        [NinjaScriptProperty]
        [XmlIgnore]
        [Display(Name = "Positive Zone Color", Description = "Color for positive GEX (support/magnet)", Order = 1, GroupName = "Colors")]
        public Brush PositiveZoneColor { get; set; }

        [Browsable(false)]
        public string PositiveZoneColorSerializable
        {
            get { return Serialize.BrushToString(PositiveZoneColor); }
            set { PositiveZoneColor = Serialize.StringToBrush(value); }
        }

        [NinjaScriptProperty]
        [XmlIgnore]
        [Display(Name = "Negative Zone Color", Description = "Color for negative GEX (accelerator)", Order = 2, GroupName = "Colors")]
        public Brush NegativeZoneColor { get; set; }

        [Browsable(false)]
        public string NegativeZoneColorSerializable
        {
            get { return Serialize.BrushToString(NegativeZoneColor); }
            set { NegativeZoneColor = Serialize.StringToBrush(value); }
        }

        [NinjaScriptProperty]
        [XmlIgnore]
        [Display(Name = "King Zone Color", Description = "Color for King node", Order = 3, GroupName = "Colors")]
        public Brush KingZoneColor { get; set; }

        [Browsable(false)]
        public string KingZoneColorSerializable
        {
            get { return Serialize.BrushToString(KingZoneColor); }
            set { KingZoneColor = Serialize.StringToBrush(value); }
        }

        [NinjaScriptProperty]
        [XmlIgnore]
        [Display(Name = "Gatekeeper Zone Color", Description = "Color for Gatekeeper node", Order = 4, GroupName = "Colors")]
        public Brush GatekeeperZoneColor { get; set; }

        [Browsable(false)]
        public string GatekeeperZoneColorSerializable
        {
            get { return Serialize.BrushToString(GatekeeperZoneColor); }
            set { GatekeeperZoneColor = Serialize.StringToBrush(value); }
        }

        [NinjaScriptProperty]
        [Range(10, 100)]
        [Display(Name = "Base Opacity", Description = "Base opacity for zones (0-100)", Order = 5, GroupName = "Colors")]
        public int BaseOpacity { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Show Status Panel", Description = "Show status in top-left corner", Order = 1, GroupName = "Status")]
        public bool ShowStatusPanel { get; set; }

        [NinjaScriptProperty]
        [Range(8, 16)]
        [Display(Name = "Status Font Size", Description = "Font size for status text", Order = 2, GroupName = "Status")]
        public int StatusFontSize { get; set; }

        #endregion
    }
}
