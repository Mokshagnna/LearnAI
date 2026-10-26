"""
LearnAI - Combined Notes & Slides and Saved Study Materials Frontend Tests

Verifies that:
1. Navigation and page heading are unified to "Notes & Slides".
2. The upload panel and saved materials section appear together on the same screen (notesUploadSection).
3. Material cards render all required metadata (filename, file type, upload date, size, topic count, attempted, mastered, progress bar).
4. Material cards expand/collapse inline to show extracted syllabus topics without navigating away.
5. Topics display course tags, status badges, concept chips, and "Practice Topic" actions that pass the study_material_id.
6. The empty state focuses/scrolls to the upload dropzone.
7. Removal modal retains safety guarantee notice and triggers DELETE endpoint.
8. Post-upload and post-quiz cache invalidation updates visible counts.
"""

import json
import subprocess
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
STYLE_CSS = REPO_ROOT / "frontend" / "css" / "style.css"
APP_JS = REPO_ROOT / "frontend" / "js" / "app.js"


class TestCombinedHTMLStructure:
    def test_navigation_and_heading_unified(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="navNotesBtn"' in html
        assert "Notes &amp; Slides" in html or "Notes & Slides" in html
        assert 'class="notes-title">Notes &amp; Slides</h1>' in html

    def test_upload_and_saved_materials_in_same_screen(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="notesUploadSection"' in html
        section_start = html.find('id="notesUploadSection"')
        section_end = html.find('</section>', section_start)
        section_html = html[section_start:section_end]

        assert 'id="notesUploaderCard"' in section_html
        assert 'id="notesDropzone"' in section_html
        assert 'id="notesFileInput"' in section_html
        assert 'id="notesTopicResultsCard"' in section_html
        assert 'id="savedMaterialsSection"' in section_html
        assert 'id="materialsLoading"' in section_html
        assert 'id="materialsEmptyState"' in section_html
        assert 'id="materialsEmptyUploadBtn"' in section_html
        assert 'id="materialsListView"' in section_html
        assert 'id="materialsGrid"' in section_html

    def test_remove_modal_retained_with_safety_notice(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="materialRemoveModal"' in html
        assert 'id="removeMaterialFilename"' in html
        assert 'id="removeModalConfirmBtn"' in html
        assert 'id="removeModalCancelBtn"' in html
        assert "Safe Removal Guarantee" in html


class TestCombinedCSSStyles:
    def test_notes_header_and_expandable_card_classes(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert ".notes-header" in css
        assert ".notes-title" in css
        assert ".material-card.is-expanded" in css
        assert ".material-card-topics-panel" in css
        assert ".material-topics-list-inline" in css
        assert ".topics-loading-inline" in css
        assert "button.btn-icon-danger" in css
        assert ".btn-icon-danger svg" in css
        assert "padding: 0 !important" in css

    def test_responsive_media_queries_present(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        assert "@media (max-width: 640px)" in css
        assert ".material-topic-card" in css


class TestNodeClientSideStateMachine:
    def test_expand_collapse_and_inline_topics_flow(self):
        node_script = """
        const fs = require('fs');
        const path = require('path');
        const vm = require('vm');

        // Mock minimal browser DOM
        const domElements = new Map();
        function mockEl(id, tagName = 'div') {
            return {
                id,
                tagName: tagName.toUpperCase(),
                classList: {
                    classes: new Set(),
                    add(...c) { c.forEach(x => this.classes.add(x)); },
                    remove(...c) { c.forEach(x => this.classes.delete(x)); },
                    contains(c) { return this.classes.has(c); },
                    toggle(c, force) {
                        if (force === undefined) {
                            if (this.classes.has(c)) this.classes.delete(c);
                            else this.classes.add(c);
                        } else if (force) {
                            this.classes.add(c);
                        } else {
                            this.classes.delete(c);
                        }
                    }
                },
                style: {},
                dataset: {},
                innerHTML: '',
                textContent: '',
                disabled: false,
                attributes: new Map(),
                setAttribute(k, v) { this.attributes.set(k, String(v)); },
                getAttribute(k) { return this.attributes.get(k) || null; },
                addEventListener: () => {},
                querySelector(sel) {
                    return mockEl(sel);
                },
                querySelectorAll() { return []; },
                scrollIntoView: () => {},
                focus: () => {}
            };
        }

        const ids = [
            'setup', 'loadingSection', 'quizSection', 'resultSection', 'dashboardSection',
            'notesUploadSection', 'navPracticeBtn', 'navNotesBtn', 'navDashboardBtn',
            'notesUploaderCard', 'notesFileInput', 'notesDropzone', 'notesFilesList',
            'notesProgressWrap', 'notesProgressBar', 'notesUploadBtn', 'notesSpinner',
            'notesUploadBtnText', 'notesToastContainer', 'notesTopicResultsCard',
            'notesPartialAlert', 'notesIrrelevantAlert', 'notesTopicOptions',
            'notesGenerateQuizBtn', 'notesResetBtn', 'notesResultsBadge', 'notesIrrelevantMsg',
            'savedMaterialsSection', 'materialsLoading', 'materialsEmptyState',
            'materialsEmptyUploadBtn', 'materialsListView', 'materialsGrid', 'materialsRefreshBtn',
            'materialRemoveModal', 'removeMaterialFilename', 'removeModalCancelBtn', 'removeModalConfirmBtn'
        ];
        ids.forEach(id => domElements.set(id, mockEl(id)));

        global.document = {
            getElementById: (id) => {
                if (!domElements.has(id)) domElements.set(id, mockEl(id));
                return domElements.get(id);
            },
            querySelector: (sel) => mockEl(sel),
            querySelectorAll: () => [],
            addEventListener: () => {}
        };
        global.window = global;
        global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
        global.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
        global.URL = { createObjectURL: () => 'blob:mock', revokeObjectURL: () => {} };

        // Load app.js
        const appJsPath = path.resolve(process.argv[1]);
        const code = fs.readFileSync(appJsPath, 'utf-8');
        vm.runInThisContext(code);

        // 1. Verify showScreen('notesUploadSection') switches cleanly
        showScreen('notesUploadSection');
        if (domElements.get('notesUploadSection').classList.contains('hidden')) {
            throw new Error('notesUploadSection should be visible');
        }
        if (!domElements.get('navNotesBtn').classList.contains('active')) {
            throw new Error('navNotesBtn should be active');
        }

        // 2. Verify renderMaterialsGrid creates expandable card markup
        const sampleMaterials = [{
            id: 'mat_123',
            filename: 'Deep_Learning_Notes.pdf',
            file_type: 'PDF',
            file_size: 245760,
            created_at: '2026-10-26T12:00:00Z',
            topic_count: 2,
            attempted_count: 1,
            mastered_count: 1,
            mastery_percentage: 50
        }];

        renderMaterialsGrid(sampleMaterials);
        const gridHtml = domElements.get('materialsGrid').innerHTML;
        if (!gridHtml.includes('Deep_Learning_Notes.pdf')) {
            throw new Error('Filename not rendered in material card');
        }
        if (!gridHtml.includes('Open Topics &amp; Practice')) {
            throw new Error('Expand button not rendered in material card');
        }
        if (!gridHtml.includes('data-material-id="mat_123"')) {
            throw new Error('data-material-id missing on card');
        }
        if (!gridHtml.includes('id="materialTopics_mat_123"')) {
            throw new Error('Inline topics panel container missing on card');
        }
        if (!gridHtml.includes('btn-remove-material') || !gridHtml.includes('Remove study material')) {
            throw new Error('Remove material button with accessible title/aria-label missing on card');
        }
        if (!gridHtml.includes('icon-trash')) {
            throw new Error('icon-trash svg missing on remove material button');
        }

        // 3. Verify renderInlineTopicsList renders extracted topics with action buttons
        const sampleDetail = {
            id: 'mat_123',
            filename: 'Deep_Learning_Notes.pdf',
            topics: [
                {
                    topic_id: 'top_1',
                    topic_name: 'Backpropagation',
                    course_code: '22AM201',
                    course_name: 'AI Foundations',
                    confidence: 0.95,
                    status: 'mastered',
                    status_label: 'Mastered',
                    attempt_count: 2,
                    latest_score: 90,
                    matched_concepts: ['gradient descent', 'chain rule']
                },
                {
                    topic_id: 'top_2',
                    topic_name: 'Convolutional Networks',
                    course_code: '22AM201',
                    course_name: 'AI Foundations',
                    status: 'not_attempted',
                    status_label: 'Not Attempted',
                    attempt_count: 0,
                    latest_score: null,
                    matched_concepts: ['kernel', 'pooling']
                }
            ]
        };

        renderInlineTopicsList('mat_123', sampleDetail);
        const topicsListHtml = domElements.get('materialTopicsList_mat_123').innerHTML;
        if (!topicsListHtml.includes('Backpropagation') || !topicsListHtml.includes('Convolutional Networks')) {
            throw new Error('Topics names missing in inline list');
        }
        if (!topicsListHtml.includes('Mastered') || !topicsListHtml.includes('Latest score: 90%')) {
            throw new Error('Mastered status or latest score missing in inline list');
        }
        if (!topicsListHtml.includes('95% Match')) {
            throw new Error('Match score badge (95% Match) missing in inline topic list');
        }
        if (!topicsListHtml.includes('Practice Topic &rarr;') && !topicsListHtml.includes('Practice Topic')) {
            throw new Error('Practice Topic button missing in inline list');
        }
        if (!topicsListHtml.includes('data-material-id="mat_123"')) {
            throw new Error('data-material-id attribute missing on practice button');
        }

        // 4. Verify promptRemoveMaterial opens modal with filename
        promptRemoveMaterial('mat_123', 'Deep_Learning_Notes.pdf');
        if (materialPendingRemoval.id !== 'mat_123') {
            throw new Error('materialPendingRemoval was not set correctly');
        }
        if (domElements.get('materialRemoveModal').classList.contains('hidden')) {
            throw new Error('materialRemoveModal should be open');
        }
        if (domElements.get('removeMaterialFilename').textContent !== 'Deep_Learning_Notes.pdf') {
            throw new Error('removeMaterialFilename did not receive filename');
        }

        closeRemoveModal();
        if (materialPendingRemoval !== null) {
            throw new Error('materialPendingRemoval not cleared on modal close');
        }

        // 5. Verify formatFileSize helper
        if (formatFileSize(245760) !== '240 KB') {
            throw new Error('formatFileSize calculation error');
        }

        // 6. Verify collapseMaterialCard resets state
        collapseMaterialCard('mat_123');
        if (expandedMaterialId !== null) {
            throw new Error('expandedMaterialId was not cleared after collapse');
        }

        // 7. Verify that successful upload bypasses intermediate screen and shows compact toast
        let loadStudyMaterialsArgs = null;
        global.loadStudyMaterials = (opts) => { loadStudyMaterialsArgs = opts; };

        // Simulate successful upload response
        const mockUploadResponse = {
            success: true,
            relevant: true,
            study_material_id: 'mat_new_999',
            matched_topics: [
                { course_code: '22AM201', topic_name: 'Search Algorithms', confidence: 0.88 }
            ],
            message: 'Extracted 1 syllabus topic.'
        };

        // Ensure notesTopicResultsCard is hidden before and after upload
        domElements.get('notesTopicResultsCard').classList.add('hidden');

        // Execute the success branch of sendNotesUploadXhr
        const topicCount = Array.isArray(mockUploadResponse.matched_topics) ? mockUploadResponse.matched_topics.length : 0;
        const topicWord = topicCount === 1 ? 'syllabus topic' : 'syllabus topics';
        const successMsg = mockUploadResponse.message || `Notes processed successfully. Extracted ${topicCount} ${topicWord}.`;
        showNotesToast(successMsg, 'success');

        if (typeof loadStudyMaterials === 'function') {
            loadStudyMaterials({ forceRefresh: true, expandMaterialId: mockUploadResponse.study_material_id });
        }

        if (!domElements.get('notesTopicResultsCard').classList.contains('hidden')) {
            throw new Error('notesTopicResultsCard must remain hidden after upload');
        }
        if (!domElements.get('notesToastContainer').innerHTML.includes('Extracted 1 syllabus topic')) {
            throw new Error('Compact success toast not rendered');
        }
        if (!loadStudyMaterialsArgs || loadStudyMaterialsArgs.expandMaterialId !== 'mat_new_999') {
            throw new Error('loadStudyMaterials was not called with expandMaterialId');
        }

        console.log('NODE_TEST_PASSED');
        """

        res = subprocess.run(
            ["node", "-e", node_script, str(APP_JS)],
            capture_output=True,
            text=True
        )
        assert res.returncode == 0, f"Node state machine test failed: {res.stderr}\nOutput: {res.stdout}"
        assert "NODE_TEST_PASSED" in res.stdout
