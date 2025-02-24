self.addEventListener('push', event => {
    const data = event.data.json();
    const title = data.title || 'New Convective Outlook';
    const options = {
        body: data.body || 'A new convective outlook has been issued.',
        icon: '/icon.png',  // Optional: Add an icon file
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(clients.openWindow('https://handry6.github.io/ConvectiveOutlookNew/interactive_map.html'));
});