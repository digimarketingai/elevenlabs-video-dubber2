(original, translated, mode) => {
    const normalize = value => {
        const data = Array.isArray(value)
            ? value
            : (value?.data || []);

        return data.map(row => [
            Number(row[0]),
            Number(row[1]),
            String(row[2] ?? "").trim()
        ]).filter(row =>
            Number.isFinite(row[0]) &&
            Number.isFinite(row[1]) &&
            row[0] >= 0 &&
            row[1] > row[0] &&
            row[2]
        );
    };

    const sourceRows = normalize(original);
    const targetRows = normalize(translated);

    let selected = [];

    if (mode === "original") selected = sourceRows;
    if (mode === "translated") selected = targetRows;
    if (mode === "bilingual") {
        selected = [...sourceRows, ...targetRows];
    }

    // Keep pathological pasted tables from freezing the browser.
    selected = selected.slice(0, 4000);

    const points = [
        ...new Set(selected.flatMap(row => [row[0], row[1]]))
    ].sort((a, b) => a - b);

    const cues = [];

    for (let i = 0; i + 1 < points.length; i++) {
        const start = points[i];
        const end = points[i + 1];

        const text = selected
            .filter(row => row[0] < end && row[1] > start)
            .map(row => row[2])
            .join("\n");

        if (!text) continue;

        const previous = cues[cues.length - 1];

        if (
            previous &&
            previous[1] === start &&
            previous[2] === text
        ) {
            previous[1] = end;
        } else {
            cues.push([start, end, text]);
        }
    }

    const state = window.__videoDubberV2 ||= {
        version: 0,
        appliedVersion: -1,
        cues: [],
        video: null,
        source: null,
        stats: ""
    };

    state.cues = cues;
    state.version++;
    state.stats =
        `原文 / Original: ${sourceRows.length} · ` +
        `翻譯 / Translated: ${targetRows.length} · ` +
        `顯示區段 / Display intervals: ${cues.length}`;

    if (!state.timer) {
        state.timer = setInterval(() => {
            const stats = document.querySelector("#caption-stats");
            if (stats) stats.textContent = state.stats;

            const video = document.querySelector("#editor-video video");
            if (!video || !video.currentSrc) return;

            if (
                state.video === video &&
                state.source === video.currentSrc &&
                state.appliedVersion === state.version
            ) {
                return;
            }

            const track = video.__editableCaptionTrack ||=
                video.addTextTrack(
                    "subtitles",
                    "字幕 / Editable captions",
                    "und"
                );

            track.mode = "hidden";

            for (const cue of Array.from(track.cues || [])) {
                track.removeCue(cue);
            }

            const escape = text => text
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;");

            for (const [start, end, text] of state.cues) {
                try {
                    const cue = new VTTCue(start, end, escape(text));
                    cue.align = "center";
                    track.addCue(cue);
                } catch (_) {
                    // Export performs stricter server-side validation.
                }
            }

            track.mode = state.cues.length ? "showing" : "disabled";

            state.video = video;
            state.source = video.currentSrc;
            state.appliedVersion = state.version;
        }, 250);
    }

    return [];
}
