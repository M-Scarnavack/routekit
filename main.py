import csv
from datetime import datetime, timedelta
from hash_table import HashTable
from truck import Truck
from package import Package

# Global data
distance_data = []
address_data = []

# 1) Distance data
def load_distance_data(file_path):
    global distance_data
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        distance_data = [
            [float(x) if x else 0.0 for x in row]
            for row in reader
        ]

# 2) Address data
def load_address_data(file_path):
    global address_data
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        address_data = [row for row in reader]

# 3) Package data
def load_package_data(file_path, package_hash_table):
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # skip header row if your CSV has one
        for row in reader:
            # Adjust these indices if your CSV columns differ
            package_id = int(row[0])
            address = row[1]
            city = row[2]
            state = row[3]
            zip_code = row[4]
            deadline = row[5]
            weight_str = row[6].replace("Kilos", "").strip()
            weight = int(weight_str) if weight_str.isdigit() else 0

            status = "At Hub"
            package_obj = Package(
                package_id,
                address,
                city,
                state,
                zip_code,
                deadline,
                weight,
                status
            )
            package_hash_table.insert(package_id, package_obj)

# Get symmetrical distance
def get_distance(loc1, loc2):
    dist = distance_data[loc1][loc2]
    if dist == 0.0:
        dist = distance_data[loc2][loc1]
    return dist

# Convert address -> index
def get_address_index(address):
    for i, row in enumerate(address_data):
        # adjust if row[2] is your actual address field
        if address in row[2]:
            return i
    return -1

# Compute package status
def compute_package_status(package, current_time):
    """
    Return:
      - 'At Hub' if current_time < departure_time
      - 'Delivered at ...' if current_time >= delivery_time
      - 'En Route' otherwise
    """
    if not package.departure_time or current_time < package.departure_time:
        return "At Hub"
    elif package.delivery_time and current_time >= package.delivery_time:
        return f"Delivered at {package.delivery_time}"
    else:
        return "En Route"

# --- The key function: correct_package_9_address ONLY if user-queried time >= 10:20
def correct_package_9_address(package_hash_table, current_time):
    fix_time = datetime(2024, 12, 12, 10, 20, 0)
    if current_time >= fix_time:
        pkg9 = package_hash_table.lookup(9)
        if pkg9:
            pkg9.address = "410 S State St"
            pkg9.city = "Salt Lake City"
            pkg9.state = "UT"
            pkg9.zip_code = "84111"
            # We do NOT print anything here to avoid “spoiling” the address early.
            # But you could do: print("Corrected #9’s address at or after 10:20.")

def parse_time_input():
    user_time = input("Enter time (HH:MM:SS): ")
    (h, m, s) = user_time.split(":")
    return datetime(2024, 12, 12, int(h), int(m), int(s))

# --- Delivery function
def deliver_packages(truck):
    current_location = 0
    while truck.packages:
        try:
            next_package = min(
                truck.packages,
                key=lambda p: get_distance(current_location, get_address_index(p.address))
            )
            truck.packages.remove(next_package)

            dist = get_distance(current_location, get_address_index(next_package.address))
            truck.mileage += dist

            departure_time = truck.current_time
            travel_time = timedelta(hours=dist / truck.speed)
            delivery_time = departure_time + travel_time

            truck.current_time = delivery_time

            next_package.departure_time = departure_time
            next_package.delivery_time = delivery_time
            next_package.status = f"Delivered at {delivery_time}"

            package_hash_table.insert(next_package.package_id, next_package)

            current_location = get_address_index(next_package.address)

        except Exception as e:
            print(f"Error delivering package on truck {truck.truck_id}: {e}")
            break

# Create global hash table
package_hash_table = HashTable()

# Load data
load_distance_data("data/Distance.csv")
load_address_data("data/Address.csv")
load_package_data("data/Package.csv", package_hash_table)

# Trucks
truck1 = Truck(1, 18, 0.0, datetime(2024,12,12,8,0,0))
truck2 = Truck(2, 18, 0.0, datetime(2024,12,12,8,0,0))
truck3 = Truck(3, 18, 0.0, datetime(2024,12,12,8,0,0))

# --- MULTI-WAVE so #9 is not delivered before 10:20.
#     EXCLUDE #9 from wave 1 loads
truck1.packages = [
    package_hash_table.lookup(i)
    for i in range(1,17)
    if i != 9 and package_hash_table.lookup(i)
]
truck2.packages = [
    package_hash_table.lookup(i)
    for i in range(17,33)
    if i != 9 and package_hash_table.lookup(i)
]
truck3.packages = [
    package_hash_table.lookup(i)
    for i in range(33,41)
    if i != 9 and package_hash_table.lookup(i)
]

# Wave 1
deliver_packages(truck1)
deliver_packages(truck2)
deliver_packages(truck3)

# Now wave 1 is done. We STILL have not corrected #9's address in code. #9 is "At Hub" with the old/wrong address.
# The earliest we physically can deliver #9 is after 10:20, so let's set truck3 to depart after 10:20
truck3.current_time = max(truck3.current_time, datetime(2024,12,12,10,20,0))

# Also, if you prefer, you can skip these lines until you actually do "Option #2" or "Option #1" after 10:20
# But let's physically pick up #9 now for wave 2:
pkg9 = package_hash_table.lookup(9)  # still old address in memory
truck3.packages = [pkg9]  # only #9
deliver_packages(truck3)

# *** We have NOT forced #9’s address in code. The user interface will correct it if query_time >= 10:20. ***


def main():
    print("WGUPS Routing Program")
    while True:
        print("\nMenu:")
        print("1. Lookup a single package at a specific time")
        print("2. View statuses of all packages at a specific time")
        print("3. View total mileage of all trucks")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            try:
                package_id = int(input("Enter package ID: "))
                query_time = parse_time_input()

                # IMPORTANT: Only correct #9’s address if user_time >= 10:20
                correct_package_9_address(package_hash_table, query_time)

                pkg = package_hash_table.lookup(package_id)
                if pkg:
                    status = compute_package_status(pkg, query_time)
                    print(
                        f"Package ID: {pkg.package_id}, "
                        f"Address: {pkg.address}, "
                        f"City: {pkg.city}, "
                        f"State: {pkg.state}, "
                        f"Zip: {pkg.zip_code}, "
                        f"Deadline: {pkg.deadline}, "
                        f"Weight: {pkg.weight} lbs, "
                        f"Status: {status}"
                    )
                else:
                    print(f"Package ID {package_id} not found.")

            except Exception as e:
                print(f"Error: {e}")

        elif choice == "2":
            try:
                query_time = parse_time_input()

                # correct #9 if it's after 10:20
                correct_package_9_address(package_hash_table, query_time)

                for pid in range(1, 41):
                    p = package_hash_table.lookup(pid)
                    if p:
                        status = compute_package_status(p, query_time)
                        print(
                            f"Package ID: {p.package_id}, "
                            f"Address: {p.address}, "
                            f"City: {p.city}, "
                            f"State: {p.state}, "
                            f"Zip: {p.zip_code}, "
                            f"Deadline: {p.deadline}, "
                            f"Weight: {p.weight} lbs, "
                            f"Status: {status}"
                        )

            except Exception as e:
                print(f"Error: {e}")

        elif choice == "3":
            total_mileage = truck1.mileage + truck2.mileage + truck3.mileage
            print(f"Total mileage of all trucks: {total_mileage:.2f} miles")

        elif choice == "4":
            print("Exiting the program.")
            break

        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
