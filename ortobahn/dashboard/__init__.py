"""Dashboard SPA for campaign management."""

import json
from pathlib import Path
from typing import Any


class DashboardSPA:
    """React/Vue SPA for campaign management dashboard."""

    def __init__(self) -> None:
        """Initialize dashboard SPA."""
        self.static_dir = Path(__file__).parent / "static"
        self.static_dir.mkdir(exist_ok=True)

    def get_index_html(self) -> str:
        """Generate dashboard index HTML."""
        return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ortobahn Campaign Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { background: #2c3e50; color: white; padding: 20px; margin-bottom: 20px; }
        .nav { display: flex; gap: 20px; margin-top: 10px; }
        .nav button { background: #3498db; color: white; border: none; padding: 10px 20px; cursor: pointer; border-radius: 4px; }
        .nav button:hover { background: #2980b9; }
        .card { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .card h2 { margin-bottom: 15px; color: #2c3e50; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: bold; }
        .form-group input, .form-group textarea { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        .btn { background: #27ae60; color: white; border: none; padding: 10px 20px; cursor: pointer; border-radius: 4px; }
        .btn:hover { background: #229954; }
        .btn-danger { background: #e74c3c; }
        .btn-danger:hover { background: #c0392b; }
        .list-item { padding: 10px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
        .status { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
        .status.active { background: #d4edda; color: #155724; }
        .status.paused { background: #fff3cd; color: #856404; }
        .error { background: #f8d7da; color: #721c24; padding: 10px; border-radius: 4px; margin-bottom: 20px; }
        .chart { height: 300px; background: #f8f9fa; border-radius: 4px; display: flex; align-items: center; justify-content: center; }
    </style>
</head>
<body>
    <div id="app">
        <div class="header">
            <div class="container">
                <h1>Ortobahn Campaign Dashboard</h1>
                <div class="nav">
                    <button @click="currentView='campaigns'">Campaigns</button>
                    <button @click="currentView='calendar'">Calendar</button>
                    <button @click="currentView='agents'">Agents</button>
                    <button @click="currentView='analytics'">Analytics</button>
                    <button @click="logout" style="margin-left: auto;">Logout</button>
                </div>
            </div>
        </div>
        <div class="container">
            <div v-if="error" class="error">{{ error }}</div>
            <campaign-list v-if="currentView==='campaigns'" :campaigns="campaigns" @create="showCreateForm" @delete="deleteCampaign"></campaign-list>
            <campaign-form v-if="showForm" @submit="createCampaign" @cancel="showForm=false"></campaign-form>
            <content-calendar v-if="currentView==='calendar'" :content="calendarContent"></content-calendar>
            <agent-monitor v-if="currentView==='agents'" :agents="agents"></agent-monitor>
            <analytics-charts v-if="currentView==='analytics'" :data="analyticsData"></analytics-charts>
        </div>
    </div>
    <script src="/static/dashboard.js"></script>
</body>
</html>
        """

    def get_dashboard_js(self) -> str:
        """Generate dashboard JavaScript."""
        return """
const API_BASE = '/api';
const { createApp } = Vue;

const CampaignList = {
    props: ['campaigns'],
    template: `
        <div class="card">
            <h2>Campaigns</h2>
            <button class="btn" @click="$emit('create')">Create Campaign</button>
            <div v-for="camp in campaigns" :key="camp.id" class="list-item">
                <div>
                    <strong>{{ camp.name }}</strong>
                    <span class="status" :class="camp.status">{{ camp.status }}</span>
                </div>
                <button class="btn btn-danger" @click="$emit('delete', camp.id)">Delete</button>
            </div>
        </div>
    `
};

const CampaignForm = {
    data() { return { name: '', description: '' }; },
    template: `
        <div class="card">
            <h2>Create Campaign</h2>
            <div class="form-group"><label>Name:</label><input v-model="name" /></div>
            <div class="form-group"><label>Description:</label><textarea v-model="description"></textarea></div>
            <button class="btn" @click="$emit('submit', { name, description })">Create</button>
            <button class="btn btn-danger" @click="$emit('cancel')">Cancel</button>
        </div>
    `
};

const ContentCalendar = {
    props: ['content'],
    template: `<div class="card"><h2>Content Calendar</h2><div class="chart">Calendar View</div></div>`
};

const AgentMonitor = {
    props: ['agents'],
    template: `
        <div class="card">
            <h2>Agent Status</h2>
            <div v-for="agent in agents" :key="agent.id" class="list-item">
                <span>{{ agent.name }}</span>
                <span class="status" :class="agent.status">{{ agent.status }}</span>
            </div>
        </div>
    `
};

const AnalyticsCharts = {
    props: ['data'],
    template: `<div class="card"><h2>Analytics</h2><div class="chart">Analytics Charts</div></div>`
};

createApp({
    components: { CampaignList, CampaignForm, ContentCalendar, AgentMonitor, AnalyticsCharts },
    data() {
        return {
            currentView: 'campaigns',
            campaigns: [],
            calendarContent: [],
            agents: [],
            analyticsData: {},
            showForm: false,
            error: null
        };
    },
    methods: {
        async loadData() {
            try {
                const resp = await axios.get(`${API_BASE}/campaigns`);
                this.campaigns = resp.data;
            } catch (e) { this.error = e.message; }
        },
        showCreateForm() { this.showForm = true; },
        async createCampaign(data) {
            try {
                await axios.post(`${API_BASE}/campaigns`, data);
                this.showForm = false;
                await this.loadData();
            } catch (e) { this.error = e.message; }
        },
        async deleteCampaign(id) {
            try {
                await axios.delete(`${API_BASE}/campaigns/${id}`);
                await this.loadData();
            } catch (e) { this.error = e.message; }
        },
        logout() { window.location.href = '/logout'; }
    },
    mounted() { this.loadData(); }
}).mount('#app');
        """


def get_dashboard_html() -> str:
    """Get dashboard HTML."""
    spa = DashboardSPA()
    return spa.get_index_html()


def get_dashboard_js() -> str:
    """Get dashboard JavaScript."""
    spa = DashboardSPA()
    return spa.get_dashboard_js()
