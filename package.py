# package.py

class Package:
    def __init__(self, package_id, address, city, state, zip_code, deadline, weight, status="At Hub"):
        """
        Initialize a Package object.

        :param package_id: int - Package ID
        :param address: str - Delivery address
        :param city: str - City of delivery
        :param state: str - State of delivery
        :param zip_code: str - ZIP code of delivery
        :param deadline: str - Delivery deadline
        :param weight: int - Weight of the package in kilos
        :param status: str - Current status of the package (default: "At Hub")
        """
        self.package_id = package_id
        self.address = address
        self.city = city
        self.state = state
        self.zip_code = zip_code
        self.deadline = deadline
        self.weight = weight
        self.status = status
        self.departure_time = None
        self.delivery_time = None

    def __str__(self):
        """
        String representation of a Package object.
        """
        return (f"Package ID: {self.package_id}, Address: {self.address}, City: {self.city}, "
                f"State: {self.state}, Zip: {self.zip_code}, Deadline: {self.deadline}, "
                f"Weight: {self.weight} Kilos, Status: {self.status}, "
                f"Departure Time: {self.departure_time}, Delivery Time: {self.delivery_time}")

    def update_status(self, current_time):
        """
        Update the status of the package based on the current time (a timedelta).
        """
        # If the truck hasn't departed with this package yet
        if not self.departure_time or current_time < self.departure_time:
            self.status = "At Hub"
        # If the package was delivered before or exactly at current_time
        elif self.delivery_time and current_time >= self.delivery_time:
            self.status = f"Delivered at {self.delivery_time}"
        else:
            self.status = "En Route"
