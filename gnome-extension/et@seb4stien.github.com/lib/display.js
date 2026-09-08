// SPDX-FileCopyrightText: 2026 Sébastien Georget
// SPDX-License-Identifier: GPL-3.0-or-later

export function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return hours > 0
        ? `${hours}h ${String(minutes).padStart(2, '0')}m`
        : `${minutes}m`;
}

export function buildDisplayText(entry, elapsedSeconds, settings) {
    if (!entry)
        return '';

    const parts = [];
    if (settings.get_boolean('show-label') && entry.label)
        parts.push(entry.label);
    if (settings.get_boolean('show-estimate') && entry.estimateSeconds > 0)
        parts.push(formatDuration(entry.estimateSeconds));
    if (settings.get_boolean('show-counter'))
        parts.push(formatDuration(elapsedSeconds));
    return parts.join(' · ');
}
