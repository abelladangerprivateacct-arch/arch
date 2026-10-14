from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
import os
import jwt
import resend
from dotenv import load_dotenv
from auth_helpers import require_admin_permission
from notifications import record_admin_action

# Load environment variables
load_dotenv()

# Initialize Resend
resend.api_key = os.getenv("RESEND_API_KEY")

# MongoDB setup
client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
users_collection = db.users

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

admin_users_bp = Blueprint('admin_users', __name__, url_prefix='/api/admin')

def admin_required(permission):
    """Use the shared role-checking decorator for all admin user routes."""
    return require_admin_permission(permission)

@admin_users_bp.route('/users', methods=['GET'])
@admin_required("users:read")
def get_users():
    """Get all users with pagination and filtering"""
    try:
        # Pagination parameters
        page = max(1, request.args.get('page', 1, type=int))
        per_page = min(100, max(1, request.args.get('per_page', 50, type=int)))
        skip = (page - 1) * per_page
        
        # Filter parameters
        search = request.args.get('search', '')
        status = request.args.get('status', '')
        investment_filter = request.args.get('investment', '')
        
        # Build query
        query = {}
        
        if search:
            query['$or'] = [
                {'firstName': {'$regex': search, '$options': 'i'}},
                {'lastName': {'$regex': search, '$options': 'i'}},
                {'email': {'$regex': search, '$options': 'i'}},
                {'referralCode': {'$regex': search, '$options': 'i'}}
            ]
            
        if status == 'verified':
            query['verified'] = True
        elif status == 'unverified':
            query['verified'] = False
        elif status == 'active':
            # Users active in last 30 days
            thirty_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            thirty_days_ago = thirty_days_ago.replace(day=thirty_days_ago.day - 30)
            query['lastLogin'] = {'$gte': thirty_days_ago}
        elif status == 'inactive':
            # Users not active in last 30 days or never logged in
            thirty_days_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            thirty_days_ago = thirty_days_ago.replace(day=thirty_days_ago.day - 30)
            query['$or'] = [
                {'lastLogin': {'$lt': thirty_days_ago}},
                {'lastLogin': {'$exists': False}}
            ]
            
        if investment_filter == 'with':
            query['investments.0'] = {'$exists': True}  # Has at least one investment
        elif investment_filter == 'without':
            query['$or'] = [
                {'investments': {'$exists': False}},
                {'investments': {'$size': 0}}
            ]
        
        # Get total count
        total_users = users_collection.count_documents(query)
        
        # Get users with projection to exclude sensitive data
        users_cursor = users_collection.find(
            query,
            {
                'password': 0,  # Exclude password
                'withdrawalFeeRequests': 0  # Exclude sensitive financial data
            }
        ).sort('createdAt', -1).skip(skip).limit(per_page)
        
        users = list(users_cursor)
        
        # Convert ObjectId to string for JSON serialization
        for user in users:
            user['_id'] = str(user['_id'])
            if 'investments' in user:
                for investment in user['investments']:
                    if 'id' in investment:
                        investment['id'] = str(investment['id'])
        
        return jsonify({
            'success': True,
            'users': users,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_users,
                'pages': (total_users + per_page - 1) // per_page
            }
        }), 200
        
    except Exception as e:
        print(f"Error fetching users: {e}")
        return jsonify({'success': False, 'error': 'Failed to fetch users'}), 500

@admin_users_bp.route('/users/<user_id>', methods=['GET'])
@admin_required("users:read")
def get_user(user_id):
    """Get detailed information about a specific user"""
    try:
        user = users_collection.find_one(
            {'_id': ObjectId(user_id)},
            {'password': 0}  # Exclude password
        )
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
        # Convert ObjectId to string
        user['_id'] = str(user['_id'])
        
        return jsonify({
            'success': True,
            'user': user
        }), 200
        
    except Exception as e:
        print(f"Error fetching user: {e}")
        return jsonify({'success': False, 'error': 'Failed to fetch user'}), 500

@admin_users_bp.route('/users/<user_id>', methods=['DELETE'])
@admin_required("users:manage")
def delete_user(user_id):
    """Delete a user"""
    try:
        if not ObjectId.is_valid(user_id):
            return jsonify({'success': False, 'error': 'Invalid user ID'}), 400
        user_object_id = ObjectId(user_id)
        user = users_collection.find_one({'_id': user_object_id}, {'investments': 1, 'withdrawals': 1})
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        if user.get('investments') or user.get('withdrawals'):
            return jsonify({'success': False, 'error': 'Users with financial history cannot be deleted; preserve the transaction record'}), 409

        result = users_collection.delete_one({'_id': user_object_id})
        
        if result.deleted_count == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        record_admin_action(request.user_id, 'user.deleted', user_id=user_object_id)
            
        return jsonify({
            'success': True,
            'message': 'User deleted successfully'
        }), 200
        
    except Exception as e:
        print(f"Error deleting user: {e}")
        return jsonify({'success': False, 'error': 'Failed to delete user'}), 500

@admin_users_bp.route('/users/send-email', methods=['POST'])
@admin_required("users:manage")
def send_email_to_users():
    """Send email to selected users"""
    try:
        data = request.get_json()
        user_ids = data.get('userIds', [])
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        
        if not user_ids:
            return jsonify({'success': False, 'error': 'No users selected'}), 400
            
        if not subject or not message:
            return jsonify({'success': False, 'error': 'Subject and message are required'}), 400
        
        # Get users' emails and names
        users = []
        for user_id in user_ids:
            user = users_collection.find_one(
                {'_id': ObjectId(user_id)},
                {'email': 1, 'firstName': 1, 'lastName': 1}
            )
            if user:
                users.append(user)
        
        if not users:
            return jsonify({'success': False, 'error': 'No valid users found'}), 400
        
        sent_count = 0
        errors = []
        
        # Send individual emails to maintain privacy
        for user in users:
            try:
                # Personalize the message
                personalized_message = message.replace('{firstName}', user['firstName'])
                personalized_message = personalized_message.replace('{lastName}', user['lastName'])
                personalized_message = personalized_message.replace('{email}', user['email'])
                
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <style>
                        body {{
                            font-family: 'Inter', 'Arial', sans-serif;
                            line-height: 1.6;
                            color: #1a1a1a;
                            margin: 0;
                            padding: 0;
                            background: linear-gradient(135deg, #0f0f0f 0%, #1a1a1a 100%);
                        }}
                        .container {{
                            max-width: 600px;
                            margin: 0 auto;
                            background: rgba(17, 24, 39, 0.95);
                            backdrop-filter: blur(10px);
                            border: 1px solid rgba(255, 255, 255, 0.1);
                            border-radius: 16px;
                            padding: 40px;
                            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
                            margin-top: 20px;
                            margin-bottom: 20px;
                        }}
                        .header {{
                            text-align: center;
                            padding: 30px 0;
                            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
                            margin-bottom: 30px;
                        }}
                        .logo {{
                            font-size: 32px;
                            font-weight: 800;
                            background: linear-gradient(135deg, #81cc2a 0%, #b6ff7a 100%);
                            background-clip: text;
                            -webkit-background-clip: text;
                            -webkit-text-fill-color: transparent;
                            letter-spacing: -0.5px;
                            margin: 0;
                        }}
                        .content {{
                            color: #e5e7eb;
                        }}
                        .greeting {{
                            font-size: 24px;
                            font-weight: 600;
                            margin-bottom: 20px;
                            color: #ffffff;
                        }}
                        .message {{
                            font-size: 16px;
                            line-height: 1.7;
                            margin-bottom: 30px;
                            color: #d1d5db;
                            white-space: pre-line;
                        }}
                        .footer {{
                            margin-top: 40px;
                            padding-top: 30px;
                            border-top: 1px solid rgba(255, 255, 255, 0.1);
                            text-align: center;
                            color: #9ca3af;
                            font-size: 14px;
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1 class="logo">Capitello</h1>
                        </div>
                        
                        <div class="content">
                            <h2 class="greeting">Hello {user['firstName']}!</h2>
                            
                            <div class="message">
                                {personalized_message}
                            </div>
                        </div>
                        
                        <div class="footer">
                            <p>The Capitello Team</p>
                            <p style="margin-top: 15px; font-size: 12px; color: #6b7280;">
                                © 2025 Capitello. All rights reserved.<br>
                                Building the future of accessible investing.
                            </p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                params = {
                    "from": "Capitello Admin <adminnoreply@destinytch.com.ng>",
                    "to": user['email'],
                    "subject": subject,
                    "html": html_content
                }
                
                response = resend.Emails.send(params)
                sent_count += 1
                print(f"✅ Email sent to {user['email']}")
                
            except Exception as e:
                error_msg = f"Failed to send to {user['email']}: {str(e)}"
                errors.append(error_msg)
                print(f"❌ {error_msg}")
        
        response_data = {
            'success': True,
            'sent': sent_count,
            'total': len(users),
            'message': f'Emails sent to {sent_count} users successfully'
        }
        
        if errors:
            response_data['errors'] = errors
            response_data['message'] = f'Emails partially sent. {sent_count} successful, {len(errors)} failed'
        
        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"Error sending emails: {e}")
        return jsonify({'success': False, 'error': 'Failed to send emails'}), 500

@admin_users_bp.route('/users/stats', methods=['GET'])
@admin_required("dashboard:read")
def get_users_stats():
    """Get users statistics for dashboard"""
    try:
        total_users = users_collection.count_documents({})
        verified_users = users_collection.count_documents({'verified': True})
        
        # Users active today
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        active_today = users_collection.count_documents({
            'lastLogin': {'$gte': today}
        })
        
        # Users with investments
        with_investments = users_collection.count_documents({
            'investments.0': {'$exists': True}
        })
        
        # New users this week
        week_ago = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = week_ago.replace(day=week_ago.day - 7)
        new_this_week = users_collection.count_documents({
            'createdAt': {'$gte': week_ago}
        })
        
        return jsonify({
            'success': True,
            'stats': {
                'totalUsers': total_users,
                'verifiedUsers': verified_users,
                'activeToday': active_today,
                'withInvestments': with_investments,
                'newThisWeek': new_this_week
            }
        }), 200
        
    except Exception as e:
        print(f"Error fetching user stats: {e}")
        return jsonify({'success': False, 'error': 'Failed to fetch user statistics'}), 500