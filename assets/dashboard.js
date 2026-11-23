/* Leaflet marker rendering. Text nodes escape source data in the tooltip. */
window.evDashboard = Object.assign({}, window.evDashboard, {
    map: {
        popupOpened: function(event, context) {
            const popup = event.popup;
            const el = popup.getElement();
            const close = el && el.querySelector('.leaflet-popup-close-button');
            if (close && !close.evDismissBound) {
                close.evDismissBound = true;
                close.addEventListener('click', function() {
                    context.setProps({clickData: {action: 'dismiss_popup', at: Date.now()}});
                });
            }
            // React/Plotly children can mount after Leaflet measures the popup.
            // Recalculate only when its content size changes, including after filters.
            const content = el && el.querySelector('.leaflet-popup-content');
            if (content) {
                if (popup.evResizeCleanup) popup.evResizeCleanup();
                let frame = null, lastSize = '';
                const schedule = function() {
                    if (!popup.isOpen()) return;
                    const rect = content.getBoundingClientRect();
                    const size = Math.round(rect.width) + ':' + Math.round(rect.height);
                    if (size === lastSize) return;
                    lastSize = size;
                    if (frame !== null) cancelAnimationFrame(frame);
                    frame = requestAnimationFrame(function() {
                        frame = null;
                        if (popup.isOpen()) popup.update();
                    });
                };
                const observer = new ResizeObserver(schedule);
                observer.observe(content);
                popup.evResizeCleanup = function() {
                    observer.disconnect();
                    if (frame !== null) cancelAnimationFrame(frame);
                };
                popup.once('remove', popup.evResizeCleanup);
                schedule();
            }
        },
        mapKeydown: function(event, context) {
            if (event.originalEvent && event.originalEvent.key === 'Escape') {
                context.setProps({clickData: {action: 'dismiss_popup', at: Date.now()}});
            }
        },
        pointToLayer: function(feature, latlng) {
            const p = feature.properties;
            const marker = L.circleMarker(latlng, {
                radius: p.radius, color: p.selected ? '#122e47' : '#ffffff',
                weight: p.selected ? 3 : 1.6, fillColor: p.color,
                fillOpacity: 0.94, className: 'charger-point station-' + p.ev_id,
                bubblingMouseEvents: false
            });
            marker.on('add', function() {
                const el = marker.getElement();
                if (el) {
                    el.setAttribute('tabindex', '0');
                    el.setAttribute('role', 'button');
                    el.setAttribute('aria-label', 'Charger ' + p.ev_id + ': ' + p.label + ', ' + p.occupancy_label);
                    el.setAttribute('data-station-id', p.ev_id);
                    el.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault(); marker.fire('click', {latlng: marker.getLatLng()}, true);
                        }
                    });
                }
            });
            return marker;
        },
        onEachFeature: function(feature, layer) {
            const p = feature.properties;
            const el = document.createElement('div');
            el.className = 'station-tooltip';
            [p.label, p.operator + ' · A1 #' + p.ev_id, p.price_label, p.occupancy_label].forEach(function(text, i) {
                const row = document.createElement(i === 0 ? 'strong' : 'div');
                row.textContent = text;
                el.appendChild(row);
            });
            layer.bindTooltip(el, {direction: 'top', offset: [0, -7]});
        }
    }
});
