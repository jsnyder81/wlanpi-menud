"""Generated from wlanpi-core docs/openapi.json. Do not edit; run tools/gen_core_client.py."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

CORE_COMMIT = 'df7ac8a'
CORE_VERSION = '2.4.13'

ROUTES: frozenset[tuple[str, str]] = frozenset({
    ('POST', '/auth/token'),
    ('DELETE', '/auth/token'),
    ('POST', '/bluetooth/pair'),
    ('POST', '/bluetooth/power/{action}'),
    ('GET', '/bluetooth/status'),
    ('GET', '/network/config/'),
    ('POST', '/network/config/'),
    ('POST', '/network/config/activate/{id}'),
    ('POST', '/network/config/deactivate/{id}'),
    ('GET', '/network/config/leftovers'),
    ('POST', '/network/config/reset'),
    ('GET', '/network/config/status'),
    ('GET', '/network/config/{id}'),
    ('PATCH', '/network/config/{id}'),
    ('DELETE', '/network/config/{id}'),
    ('GET', '/network/connections/tcp'),
    ('GET', '/network/connections/udp'),
    ('GET', '/network/dhcp/leases'),
    ('GET', '/network/ethernet/all/vlan'),
    ('GET', '/network/ethernet/all/vlan/{vlan}'),
    ('GET', '/network/ethernet/{interface}'),
    ('GET', '/network/ethernet/{interface}/vlan'),
    ('GET', '/network/ethernet/{interface}/vlan/{vlan}'),
    ('POST', '/network/ethernet/{interface}/vlan/{vlan}'),
    ('DELETE', '/network/ethernet/{interface}/vlan/{vlan}'),
    ('GET', '/network/info/'),
    ('GET', '/network/info/publicip6'),
    ('GET', '/network/interfaces'),
    ('GET', '/network/interfaces/{iface}/link-stats'),
    ('POST', '/network/interfaces/{iface}/renew'),
    ('GET', '/network/interfaces/{iface}/wlan-link'),
    ('GET', '/network/interfaces/{interface}'),
    ('GET', '/network/routing'),
    ('GET', '/network/wlan/getConnected'),
    ('GET', '/network/wlan/getInterfaces'),
    ('GET', '/network/wlan/pci-drivers'),
    ('POST', '/network/wlan/revert'),
    ('GET', '/network/wlan/scan'),
    ('POST', '/network/wlan/set'),
    ('POST', '/network/wlan/set-dbus'),
    ('GET', '/network/wlan/usb-drivers'),
    ('GET', '/profiler/files'),
    ('GET', '/profiler/files/{path}'),
    ('POST', '/profiler/purge'),
    ('POST', '/profiler/start'),
    ('GET', '/profiler/status'),
    ('POST', '/profiler/stop'),
    ('GET', '/streaming/capture'),
    ('GET', '/system/battery'),
    ('GET', '/system/datetime'),
    ('GET', '/system/device/info'),
    ('GET', '/system/device/model'),
    ('GET', '/system/device/stats'),
    ('GET', '/system/health'),
    ('GET', '/system/hotspot/clients'),
    ('GET', '/system/hotspot/ssid-passphrase'),
    ('GET', '/system/journal'),
    ('POST', '/system/journal'),
    ('GET', '/system/ntp'),
    ('POST', '/system/ntp'),
    ('POST', '/system/reboot'),
    ('GET', '/system/reg-domain'),
    ('GET', '/system/reg-domain/list'),
    ('POST', '/system/reg-domain/set'),
    ('POST', '/system/service/restart'),
    ('POST', '/system/service/start'),
    ('GET', '/system/service/status'),
    ('POST', '/system/service/stop'),
    ('GET', '/system/services'),
    ('GET', '/system/services/failed'),
    ('POST', '/system/shutdown'),
    ('GET', '/system/timezone'),
    ('POST', '/system/timezone/auto'),
    ('GET', '/system/timezone/list'),
    ('POST', '/system/timezone/set'),
    ('POST', '/utils/blinker/start'),
    ('GET', '/utils/blinker/status'),
    ('POST', '/utils/blinker/stop'),
    ('GET', '/utils/pci'),
    ('GET', '/utils/reachability'),
    ('GET', '/utils/speedtest'),
    ('GET', '/utils/ufw'),
    ('GET', '/utils/usb'),
    ('GET', '/utils/wlan/scan'),
    ('GET', '/wifi/capabilities'),
    ('GET', '/wifi/hotspot/link'),
    ('GET', '/wifi/hotspot/stations'),
    ('GET', '/wifi/regulatory'),
})


def _q(value: str) -> str:
    return quote(str(value), safe="")


class GeneratedCoreAPI:
    """One method per core operation. Subclasses provide ``request``."""

    async def request(self, method: str, path: str, *, query: dict[str, Any] | None = None,
                      body: Any = None, timeout: float | None = None) -> Any:
        raise NotImplementedError

    async def post_auth_token(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /auth/token: Issue JWT bearer token. Returns Token."""
        return await self.request('POST', '/auth/token', query={}, body=body, timeout=_timeout)

    async def delete_auth_token(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """DELETE /auth/token: Revoke current JWT. Returns TokenRevokeResponse."""
        return await self.request('DELETE', '/auth/token', query={}, body=body, timeout=_timeout)

    async def post_bluetooth_pair(self, _timeout: float | None = None) -> Any:
        """POST /bluetooth/pair: Bt Pair. Returns BluetoothPairResponse."""
        return await self.request('POST', '/bluetooth/pair', query={}, timeout=_timeout)

    async def post_bluetooth_power_by_action(self, action: str, _timeout: float | None = None) -> Any:
        """POST /bluetooth/power/{action}: Bt Power. Returns PowerState."""
        return await self.request('POST', f'/bluetooth/power/{_q(action)}', query={}, timeout=_timeout)

    async def get_bluetooth_status(self, _timeout: float | None = None) -> Any:
        """GET /bluetooth/status: Btstatus. Returns BluetoothStatus."""
        return await self.request('GET', '/bluetooth/status', query={}, timeout=_timeout)

    async def get_network_config(self, _timeout: float | None = None) -> Any:
        """GET /network/config/: Get Configs. Returns object."""
        return await self.request('GET', '/network/config/', query={}, timeout=_timeout)

    async def post_network_config(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/config/: Create Config. Returns object."""
        return await self.request('POST', '/network/config/', query={}, body=body, timeout=_timeout)

    async def post_network_config_activate_by_id(self, id: str, *, override_active: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/config/activate/{id}: Activate Config. Returns ActivationResponse."""
        return await self.request('POST', f'/network/config/activate/{_q(id)}', query={'override_active': override_active}, timeout=_timeout)

    async def post_network_config_deactivate_by_id(self, id: str, *, override_active: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/config/deactivate/{id}: Deactivate Config. Returns DeactivationResponse."""
        return await self.request('POST', f'/network/config/deactivate/{_q(id)}', query={'override_active': override_active}, timeout=_timeout)

    async def get_network_config_leftovers(self, _timeout: float | None = None) -> Any:
        """GET /network/config/leftovers: Get Leftovers. Returns LeftoversResponse."""
        return await self.request('GET', '/network/config/leftovers', query={}, timeout=_timeout)

    async def post_network_config_reset(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/config/reset: Reset Namespaces. Returns NamespaceResetResponse."""
        return await self.request('POST', '/network/config/reset', query={}, body=body, timeout=_timeout)

    async def get_network_config_status(self, _timeout: float | None = None) -> Any:
        """GET /network/config/status: Get Status. Returns NetworkConfigStatus."""
        return await self.request('GET', '/network/config/status', query={}, timeout=_timeout)

    async def get_network_config_by_id(self, id: str, _timeout: float | None = None) -> Any:
        """GET /network/config/{id}: Get Config By Id. Returns NetConfigPublic."""
        return await self.request('GET', f'/network/config/{_q(id)}', query={}, timeout=_timeout)

    async def patch_network_config_by_id(self, id: str, *, body: Any = None, _timeout: float | None = None) -> Any:
        """PATCH /network/config/{id}: Update Config. Returns object."""
        return await self.request('PATCH', f'/network/config/{_q(id)}', query={}, body=body, timeout=_timeout)

    async def delete_network_config_by_id(self, id: str, *, force: Any = None, _timeout: float | None = None) -> Any:
        """DELETE /network/config/{id}: Delete Config. Returns object."""
        return await self.request('DELETE', f'/network/config/{_q(id)}', query={'force': force}, timeout=_timeout)

    async def get_network_connections_tcp(self, *, namespace: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/connections/tcp: Show Tcp Connections. Returns ConnectionsResponse."""
        return await self.request('GET', '/network/connections/tcp', query={'namespace': namespace}, timeout=_timeout)

    async def get_network_connections_udp(self, *, namespace: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/connections/udp: Show Udp Connections. Returns ConnectionsResponse."""
        return await self.request('GET', '/network/connections/udp', query={'namespace': namespace}, timeout=_timeout)

    async def get_network_dhcp_leases(self, _timeout: float | None = None) -> Any:
        """GET /network/dhcp/leases: Show Dhcp Leases. Returns DhcpLeasesResponse."""
        return await self.request('GET', '/network/dhcp/leases', query={}, timeout=_timeout)

    async def get_network_ethernet_all_vlan(self, *, interface: Any = None, vlan: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/ethernet/all/vlan: Show All Ethernet Vlans. Returns object."""
        return await self.request('GET', '/network/ethernet/all/vlan', query={'interface': interface, 'vlan': vlan}, timeout=_timeout)

    async def get_network_ethernet_all_vlan_by_vlan(self, vlan: str, *, interface: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/ethernet/all/vlan/{vlan}: Show All Ethernet Vlans. Returns object."""
        return await self.request('GET', f'/network/ethernet/all/vlan/{_q(vlan)}', query={'interface': interface}, timeout=_timeout)

    async def get_network_ethernet_by_interface(self, interface: str, _timeout: float | None = None) -> Any:
        """GET /network/ethernet/{interface}: Show All Ethernet Interfaces. Returns object."""
        return await self.request('GET', f'/network/ethernet/{_q(interface)}', query={}, timeout=_timeout)

    async def get_network_ethernet_by_interface_vlan(self, interface: str, *, vlan: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/ethernet/{interface}/vlan: Show All Ethernet Vlans. Returns object."""
        return await self.request('GET', f'/network/ethernet/{_q(interface)}/vlan', query={'vlan': vlan}, timeout=_timeout)

    async def get_network_ethernet_by_interface_vlan_by_vlan(self, interface: str, vlan: str, _timeout: float | None = None) -> Any:
        """GET /network/ethernet/{interface}/vlan/{vlan}: Show All Ethernet Vlans. Returns object."""
        return await self.request('GET', f'/network/ethernet/{_q(interface)}/vlan/{_q(vlan)}', query={}, timeout=_timeout)

    async def post_network_ethernet_by_interface_vlan_by_vlan(self, interface: str, vlan: str, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/ethernet/{interface}/vlan/{vlan}: Create Ethernet Vlan. Returns NetworkConfigResponse."""
        return await self.request('POST', f'/network/ethernet/{_q(interface)}/vlan/{_q(vlan)}', query={}, body=body, timeout=_timeout)

    async def delete_network_ethernet_by_interface_vlan_by_vlan(self, interface: str, vlan: str, *, allow_missing: Any = None, _timeout: float | None = None) -> Any:
        """DELETE /network/ethernet/{interface}/vlan/{vlan}: Delete Ethernet Vlan. Returns NetworkConfigResponse."""
        return await self.request('DELETE', f'/network/ethernet/{_q(interface)}/vlan/{_q(vlan)}', query={'allow_missing': allow_missing}, timeout=_timeout)

    async def get_network_info(self, _timeout: float | None = None) -> Any:
        """GET /network/info/: Show Network Info. Returns NetworkInfo."""
        return await self.request('GET', '/network/info/', query={}, timeout=_timeout)

    async def get_network_info_publicip6(self, _timeout: float | None = None) -> Any:
        """GET /network/info/publicip6: Show Public Ip6. Returns PublicIpInfo."""
        return await self.request('GET', '/network/info/publicip6', query={}, timeout=_timeout)

    async def get_network_interfaces(self, *, interface: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/interfaces: Show All Interfaces. Returns object."""
        return await self.request('GET', '/network/interfaces', query={'interface': interface}, timeout=_timeout)

    async def get_network_interfaces_by_iface_link_stats(self, iface: str, _timeout: float | None = None) -> Any:
        """GET /network/interfaces/{iface}/link-stats: Show Interface Link Stats. Returns LinkStats."""
        return await self.request('GET', f'/network/interfaces/{_q(iface)}/link-stats', query={}, timeout=_timeout)

    async def post_network_interfaces_by_iface_renew(self, iface: str, _timeout: float | None = None) -> Any:
        """POST /network/interfaces/{iface}/renew: Renew Interface Dhcp. Returns DhcpRenewResponse."""
        return await self.request('POST', f'/network/interfaces/{_q(iface)}/renew', query={}, timeout=_timeout)

    async def get_network_interfaces_by_iface_wlan_link(self, iface: str, _timeout: float | None = None) -> Any:
        """GET /network/interfaces/{iface}/wlan-link: Show Interface Wlan Link. Returns WlanLink."""
        return await self.request('GET', f'/network/interfaces/{_q(iface)}/wlan-link', query={}, timeout=_timeout)

    async def get_network_interfaces_by_interface(self, interface: str, _timeout: float | None = None) -> Any:
        """GET /network/interfaces/{interface}: Show All Interfaces. Returns object."""
        return await self.request('GET', f'/network/interfaces/{_q(interface)}', query={}, timeout=_timeout)

    async def get_network_routing(self, *, namespace: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/routing: Show Routing Table. Returns RoutingTable."""
        return await self.request('GET', '/network/routing', query={'namespace': namespace}, timeout=_timeout)

    async def get_network_wlan_get_connected(self, *, interface: Any = None, timeout: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/wlan/getConnected: [Deprecated] Connected network details. Returns ConnectedNetwork."""
        return await self.request('GET', '/network/wlan/getConnected', query={'interface': interface, 'timeout': timeout}, timeout=_timeout)

    async def get_network_wlan_get_interfaces(self, *, timeout: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/wlan/getInterfaces: [Deprecated] List wireless interfaces. Returns Interfaces."""
        return await self.request('GET', '/network/wlan/getInterfaces', query={'timeout': timeout}, timeout=_timeout)

    async def get_network_wlan_pci_drivers(self, _timeout: float | None = None) -> Any:
        """GET /network/wlan/pci-drivers: Show Wlan Pci Drivers. Returns WlanPciDriversResponse."""
        return await self.request('GET', '/network/wlan/pci-drivers', query={}, timeout=_timeout)

    async def post_network_wlan_revert(self, *, timeout: Any = None, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/wlan/revert: Revert Wlan Namespace. Returns RevertNamespace."""
        return await self.request('POST', '/network/wlan/revert', query={'timeout': timeout}, body=body, timeout=_timeout)

    async def get_network_wlan_scan(self, *, type: Any = None, interface: Any = None, timeout: Any = None, _timeout: float | None = None) -> Any:
        """GET /network/wlan/scan: [Deprecated] WLAN scan. Returns ScanResults."""
        return await self.request('GET', '/network/wlan/scan', query={'type': type, 'interface': interface, 'timeout': timeout}, timeout=_timeout)

    async def post_network_wlan_set(self, *, timeout: Any = None, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/wlan/set: [Deprecated] Namespace stub — removed. Returns any."""
        return await self.request('POST', '/network/wlan/set', query={'timeout': timeout}, body=body, timeout=_timeout)

    async def post_network_wlan_set_dbus(self, *, timeout: Any = None, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /network/wlan/set-dbus: [Deprecated] DBus network setup — removed. Returns any."""
        return await self.request('POST', '/network/wlan/set-dbus', query={'timeout': timeout}, body=body, timeout=_timeout)

    async def get_network_wlan_usb_drivers(self, _timeout: float | None = None) -> Any:
        """GET /network/wlan/usb-drivers: Show Wlan Usb Drivers. Returns WlanUsbDriversResponse."""
        return await self.request('GET', '/network/wlan/usb-drivers', query={}, timeout=_timeout)

    async def get_profiler_files(self, *, mac: Any = None, _timeout: float | None = None) -> Any:
        """GET /profiler/files: Profiler Files. Returns Files."""
        return await self.request('GET', '/profiler/files', query={'mac': mac}, timeout=_timeout)

    async def get_profiler_files_by_path(self, path: str, _timeout: float | None = None) -> Any:
        """GET /profiler/files/{path}: Profiler File. Returns any."""
        return await self.request('GET', f'/profiler/files/{_q(path)}', query={}, timeout=_timeout)

    async def post_profiler_purge(self, _timeout: float | None = None) -> Any:
        """POST /profiler/purge: Purge Profiler. Returns Purge."""
        return await self.request('POST', '/profiler/purge', query={}, timeout=_timeout)

    async def post_profiler_start(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /profiler/start: Start Profiler. Returns Start-Output."""
        return await self.request('POST', '/profiler/start', query={}, body=body, timeout=_timeout)

    async def get_profiler_status(self, _timeout: float | None = None) -> Any:
        """GET /profiler/status: Profiler Status. Returns Status."""
        return await self.request('GET', '/profiler/status', query={}, timeout=_timeout)

    async def post_profiler_stop(self, _timeout: float | None = None) -> Any:
        """POST /profiler/stop: Stop Profiler. Returns Stop."""
        return await self.request('POST', '/profiler/stop', query={}, timeout=_timeout)

    async def get_streaming_capture(self, _timeout: float | None = None) -> Any:
        """GET /streaming/capture: Packet capture WebSocket. Returns any."""
        return await self.request('GET', '/streaming/capture', query={}, timeout=_timeout)

    async def get_system_battery(self, _timeout: float | None = None) -> Any:
        """GET /system/battery: Show Battery. Returns BatteryInfo."""
        return await self.request('GET', '/system/battery', query={}, timeout=_timeout)

    async def get_system_datetime(self, _timeout: float | None = None) -> Any:
        """GET /system/datetime: Show Datetime. Returns DateTimeInfo."""
        return await self.request('GET', '/system/datetime', query={}, timeout=_timeout)

    async def get_system_device_info(self, _timeout: float | None = None) -> Any:
        """GET /system/device/info: Show Device Info. Returns DeviceInfo."""
        return await self.request('GET', '/system/device/info', query={}, timeout=_timeout)

    async def get_system_device_model(self, _timeout: float | None = None) -> Any:
        """GET /system/device/model: Show Device Model. Returns DeviceModel."""
        return await self.request('GET', '/system/device/model', query={}, timeout=_timeout)

    async def get_system_device_stats(self, _timeout: float | None = None) -> Any:
        """GET /system/device/stats: Device Stats. Returns DeviceStats."""
        return await self.request('GET', '/system/device/stats', query={}, timeout=_timeout)

    async def get_system_health(self, _timeout: float | None = None) -> Any:
        """GET /system/health: Show Health. Returns Health."""
        return await self.request('GET', '/system/health', query={}, timeout=_timeout)

    async def get_system_hotspot_clients(self, *, iface: Any = None, _timeout: float | None = None) -> Any:
        """GET /system/hotspot/clients: Hotspot connected client count. Returns HotspotClients."""
        return await self.request('GET', '/system/hotspot/clients', query={'iface': iface}, timeout=_timeout)

    async def get_system_hotspot_ssid_passphrase(self, _timeout: float | None = None) -> Any:
        """GET /system/hotspot/ssid-passphrase: Hotspot SSID and WPA passphrase. Returns HotspotCredentials."""
        return await self.request('GET', '/system/hotspot/ssid-passphrase', query={}, timeout=_timeout)

    async def get_system_journal(self, _timeout: float | None = None) -> Any:
        """GET /system/journal: Show Journal. Returns JournalInfo."""
        return await self.request('GET', '/system/journal', query={}, timeout=_timeout)

    async def post_system_journal(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/journal: Set Journal. Returns JournalInfo."""
        return await self.request('POST', '/system/journal', query={}, body=body, timeout=_timeout)

    async def get_system_ntp(self, _timeout: float | None = None) -> Any:
        """GET /system/ntp: Show Ntp. Returns NtpInfo."""
        return await self.request('GET', '/system/ntp', query={}, timeout=_timeout)

    async def post_system_ntp(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/ntp: Set Ntp. Returns NtpInfo."""
        return await self.request('POST', '/system/ntp', query={}, body=body, timeout=_timeout)

    async def post_system_reboot(self, _timeout: float | None = None) -> Any:
        """POST /system/reboot: Reboot Device. Returns PowerActionResponse."""
        return await self.request('POST', '/system/reboot', query={}, timeout=_timeout)

    async def get_system_reg_domain(self, _timeout: float | None = None) -> Any:
        """GET /system/reg-domain: Show Reg Domain. Returns RegDomainInfo."""
        return await self.request('GET', '/system/reg-domain', query={}, timeout=_timeout)

    async def get_system_reg_domain_list(self, _timeout: float | None = None) -> Any:
        """GET /system/reg-domain/list: List Reg Domains. Returns RegDomainList."""
        return await self.request('GET', '/system/reg-domain/list', query={}, timeout=_timeout)

    async def post_system_reg_domain_set(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/reg-domain/set: Set Reg Domain. Returns RegDomainInfo."""
        return await self.request('POST', '/system/reg-domain/set', query={}, body=body, timeout=_timeout)

    async def post_system_service_restart(self, *, name: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/service/restart: Restart A Systemd Service. Returns ServiceRunning."""
        return await self.request('POST', '/system/service/restart', query={'name': name}, timeout=_timeout)

    async def post_system_service_start(self, *, name: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/service/start: Start A Systemd Service. Returns ServiceRunning."""
        return await self.request('POST', '/system/service/start', query={'name': name}, timeout=_timeout)

    async def get_system_service_status(self, *, name: Any = None, _timeout: float | None = None) -> Any:
        """GET /system/service/status: Show A Systemd Service Status. Returns ServiceStatus."""
        return await self.request('GET', '/system/service/status', query={'name': name}, timeout=_timeout)

    async def post_system_service_stop(self, *, name: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/service/stop: Stop A Systemd Service. Returns ServiceRunning."""
        return await self.request('POST', '/system/service/stop', query={'name': name}, timeout=_timeout)

    async def get_system_services(self, _timeout: float | None = None) -> Any:
        """GET /system/services: Show Allowed Services. Returns AllowedServices."""
        return await self.request('GET', '/system/services', query={}, timeout=_timeout)

    async def get_system_services_failed(self, _timeout: float | None = None) -> Any:
        """GET /system/services/failed: Show Failed Services. Returns FailedServices."""
        return await self.request('GET', '/system/services/failed', query={}, timeout=_timeout)

    async def post_system_shutdown(self, _timeout: float | None = None) -> Any:
        """POST /system/shutdown: Shutdown Device. Returns PowerActionResponse."""
        return await self.request('POST', '/system/shutdown', query={}, timeout=_timeout)

    async def get_system_timezone(self, _timeout: float | None = None) -> Any:
        """GET /system/timezone: Show Timezone. Returns TimezoneInfo."""
        return await self.request('GET', '/system/timezone', query={}, timeout=_timeout)

    async def post_system_timezone_auto(self, _timeout: float | None = None) -> Any:
        """POST /system/timezone/auto: Enable Timezone Auto. Returns NtpAutoInfo."""
        return await self.request('POST', '/system/timezone/auto', query={}, timeout=_timeout)

    async def get_system_timezone_list(self, _timeout: float | None = None) -> Any:
        """GET /system/timezone/list: List Timezones. Returns TimezoneList."""
        return await self.request('GET', '/system/timezone/list', query={}, timeout=_timeout)

    async def post_system_timezone_set(self, *, body: Any = None, _timeout: float | None = None) -> Any:
        """POST /system/timezone/set: Set Timezone. Returns TimezoneInfo."""
        return await self.request('POST', '/system/timezone/set', query={}, body=body, timeout=_timeout)

    async def post_utils_blinker_start(self, *, interface: Any = None, _timeout: float | None = None) -> Any:
        """POST /utils/blinker/start: Start Blinker. Returns BlinkerActionResponse."""
        return await self.request('POST', '/utils/blinker/start', query={'interface': interface}, timeout=_timeout)

    async def get_utils_blinker_status(self, _timeout: float | None = None) -> Any:
        """GET /utils/blinker/status: Blinker Status. Returns BlinkerStatus."""
        return await self.request('GET', '/utils/blinker/status', query={}, timeout=_timeout)

    async def post_utils_blinker_stop(self, _timeout: float | None = None) -> Any:
        """POST /utils/blinker/stop: Stop Blinker. Returns BlinkerActionResponse."""
        return await self.request('POST', '/utils/blinker/stop', query={}, timeout=_timeout)

    async def get_utils_pci(self, _timeout: float | None = None) -> Any:
        """GET /utils/pci: Pci Devices. Returns Pci."""
        return await self.request('GET', '/utils/pci', query={}, timeout=_timeout)

    async def get_utils_reachability(self, *, targets: Any = None, namespace: Any = None, _timeout: float | None = None) -> Any:
        """GET /utils/reachability: Reachability. Returns ReachabilityTest."""
        return await self.request('GET', '/utils/reachability', query={'targets': targets, 'namespace': namespace}, timeout=_timeout)

    async def get_utils_speedtest(self, _timeout: float | None = None) -> Any:
        """GET /utils/speedtest: Internet speed test (slow). Returns SpeedTest."""
        return await self.request('GET', '/utils/speedtest', query={}, timeout=_timeout)

    async def get_utils_ufw(self, _timeout: float | None = None) -> Any:
        """GET /utils/ufw: Ufw Information. Returns Ufw."""
        return await self.request('GET', '/utils/ufw', query={}, timeout=_timeout)

    async def get_utils_usb(self, _timeout: float | None = None) -> Any:
        """GET /utils/usb: Usb Interfaces. Returns Usb."""
        return await self.request('GET', '/utils/usb', query={}, timeout=_timeout)

    async def get_utils_wlan_scan(self, *, iface: Any = None, namespace: Any = None, hidden: Any = None, detail: Any = None, _timeout: float | None = None) -> Any:
        """GET /utils/wlan/scan: WLAN scan (canonical). Returns WlanScanResponse."""
        return await self.request('GET', '/utils/wlan/scan', query={'iface': iface, 'namespace': namespace, 'hidden': hidden, 'detail': detail}, timeout=_timeout)

    async def get_wifi_capabilities(self, _timeout: float | None = None) -> Any:
        """GET /wifi/capabilities: Show Wi-Fi capabilities. Returns WifiCapabilitiesResponse."""
        return await self.request('GET', '/wifi/capabilities', query={}, timeout=_timeout)

    async def get_wifi_hotspot_link(self, *, iface: Any = None, _timeout: float | None = None) -> Any:
        """GET /wifi/hotspot/link: Hotspot per-client link stats. Returns HotspotClientLinkResponse."""
        return await self.request('GET', '/wifi/hotspot/link', query={'iface': iface}, timeout=_timeout)

    async def get_wifi_hotspot_stations(self, *, iface: Any = None, _timeout: float | None = None) -> Any:
        """GET /wifi/hotspot/stations: Hotspot AP station list. Returns HotspotStationsResponse."""
        return await self.request('GET', '/wifi/hotspot/stations', query={'iface': iface}, timeout=_timeout)

    async def get_wifi_regulatory(self, _timeout: float | None = None) -> Any:
        """GET /wifi/regulatory: Show Wi-Fi regulatory information. Returns WifiRegulatoryResponse."""
        return await self.request('GET', '/wifi/regulatory', query={}, timeout=_timeout)
