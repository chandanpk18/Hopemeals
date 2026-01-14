# **HopeMeals**
**HopeMeals** is a food donation and redistribution platform built on Django. It bridges donors, NGOs, and receivers to reduce food waste and support communities. The system supports real-time food tracking with map integration, email notifications, ratings, and different user roles. 
# User Roles & Responsibilities 
* Admin (Django Admin): Manages platform configuration and users. 
* NGO: Mediator; validates donated food, manages inventory, delivers to receivers, provides donor ratings. 
* Donor: Posts surplus food details, shares pickup location, receives rating and notifications. 
* Receiver: Requests food from NGOs, provides donor ratings, shares delivery location. 

# Core Features 
1. Authentication & User Management 
Custom registration and login for each user type. 
Role-based views/access controls. 

2. Food Donation Workflow 
Donor posts food: Details, quality, expiry, quantity, location. 
NGO reviews & accepts: Validates quality, accepts or rejects. If accepted, donor is notified via email. 
Inventory update: Accepted food added to NGO’s available items. 

3. Food Request & Allocation 
Receiver requests food: Specifies number of people & delivery address.
NGO assigns food: Matches surplus to receiver needs, confirms delivery. 

4. Email Notifications 
On new donation, status updates, allocation, delivery, and feedback requests. 
Automated using Django’s email backend. 

5. Ratings & Comments 
Both NGOs and receivers can rate donors (1 to 5 stars), add comments. 
Ratings visible in donor profiles for transparency. 

6. Map Integration & Live Tracking 
Pickup location: Donor provides geolocation upon food posting. 
Delivery tracking: Once NGO picks up, live location is shared with the receiver (similar to Uber/Swiggy approach). 
Utilizes Google Maps API or Mapbox for route visualization and live tracking. 

Database Models (High-level) 
Model Key Fields 
User name, email, password, role (Admin/NGO/Donor/Receiver), contact 
Food donor (FK), details, quantity, status, pickup_location, NGO (FK) 
NGOInventory ngo (FK), food (FK), quantity, updated_at 
FoodRequest receiver (FK), ngo (FK), food (FK), people_count, status 
Rating giver (FK), receiver (FK), stars (int), comment, role 
Delivery ngo (FK), food (FK), receiver (FK), status, route, live_location
